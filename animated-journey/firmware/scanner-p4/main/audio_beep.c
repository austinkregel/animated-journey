#include "audio_beep.h"

#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"

static const char *TAG = "audio_beep";

/*
 * ESP32-P4-NANO audio hardware:
 *   ES8311 codec on I2C (SDA=7, SCL=8, addr=0x18 / 0x30 8-bit)
 *   I2S data path: MCLK=13, BCLK=12, LRCK=10, DOUT=9
 *   NS4150B PA enable: GPIO53 (active high)
 */
#define AUDIO_I2C_SDA       7
#define AUDIO_I2C_SCL       8
#define AUDIO_I2S_MCLK      13
#define AUDIO_I2S_BCLK      12
#define AUDIO_I2S_LRCK      10
#define AUDIO_I2S_DOUT      9
#define PA_ENABLE_GPIO      53
#define ES8311_I2C_ADDR_7BIT  0x18
#define ES8311_I2C_ADDR_8BIT  0x30

#define SAMPLE_RATE         16000
#define BITS_PER_SAMPLE     16
#define MCLK_MULTIPLE       256

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static bool s_initialized = false;
static bool s_playing = false;
static SemaphoreHandle_t s_play_mutex = NULL;

static i2s_chan_handle_t s_i2s_tx = NULL;
static esp_codec_dev_handle_t s_codec_dev = NULL;

typedef struct {
    int freq_hz;
    int duration_ms;
    int gap_ms;
    int repeats;
} beep_params_t;

static void pa_enable(bool on)
{
    gpio_set_level(PA_ENABLE_GPIO, on ? 1 : 0);
}

esp_err_t audio_beep_init(void)
{
    s_play_mutex = xSemaphoreCreateMutex();
    configASSERT(s_play_mutex);

    /* PA GPIO (NS4150B CTRL) -- start disabled */
    gpio_config_t pa_cfg = {
        .pin_bit_mask = 1ULL << PA_ENABLE_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&pa_cfg);
    pa_enable(false);

    /* I2C master bus for ES8311 control */
    i2c_master_bus_config_t i2c_bus_cfg = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = I2C_NUM_0,
        .sda_io_num = AUDIO_I2C_SDA,
        .scl_io_num = AUDIO_I2C_SCL,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t i2c_bus = NULL;
    esp_err_t ret = i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2C master bus init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    /* Probe ES8311 on the bus (i2c_master_probe uses 7-bit address) */
    ret = i2c_master_probe(i2c_bus, ES8311_I2C_ADDR_7BIT, 100);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ES8311 not found at I2C addr 0x%02X: %s", ES8311_I2C_ADDR_7BIT, esp_err_to_name(ret));
        ESP_LOGW(TAG, "Audio beep disabled (codec not responding)");
        return ret;
    }
    ESP_LOGI(TAG, "ES8311 found at I2C addr 0x%02X (7-bit)", ES8311_I2C_ADDR_7BIT);

    /* I2S TX channel */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 240;
    ret = i2s_new_channel(&chan_cfg, &s_i2s_tx, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S channel create failed: %s", esp_err_to_name(ret));
        return ret;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = AUDIO_I2S_MCLK,
            .bclk = AUDIO_I2S_BCLK,
            .ws   = AUDIO_I2S_LRCK,
            .dout = AUDIO_I2S_DOUT,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.clk_cfg.mclk_multiple = MCLK_MULTIPLE;

    ret = i2s_channel_init_std_mode(s_i2s_tx, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S STD init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    /* ES8311 codec via esp_codec_dev (addr field expects 8-bit / left-shifted format) */
    audio_codec_i2c_cfg_t i2c_cfg = {
        .addr = ES8311_I2C_ADDR_8BIT,
        .bus_handle = i2c_bus,
    };
    const audio_codec_ctrl_if_t *ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();

    es8311_codec_cfg_t es_cfg = {
        .ctrl_if = ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = PA_ENABLE_GPIO,
        .pa_reverted = false,
        .master_mode = false,
        .use_mclk = true,
        .digital_mic = false,
        .invert_mclk = false,
        .invert_sclk = false,
    };
    const audio_codec_if_t *codec_if = es8311_codec_new(&es_cfg);
    if (codec_if == NULL) {
        ESP_LOGE(TAG, "ES8311 codec init failed");
        return ESP_FAIL;
    }

    /* I2S data interface for codec dev */
    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_1,
        .tx_handle = s_i2s_tx,
        .rx_handle = NULL,
    };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_cfg);

    esp_codec_dev_cfg_t dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = codec_if,
        .data_if = data_if,
    };
    s_codec_dev = esp_codec_dev_new(&dev_cfg);
    if (s_codec_dev == NULL) {
        ESP_LOGE(TAG, "esp_codec_dev_new failed");
        return ESP_FAIL;
    }

    s_initialized = true;
    ESP_LOGI(TAG, "Audio beep initialized (ES8311 + NS4150B on GPIO%d)", PA_ENABLE_GPIO);
    return ESP_OK;
}

static void generate_tone(int16_t *buf, int num_samples, int freq_hz, int sample_rate)
{
    for (int i = 0; i < num_samples; i++) {
        double t = (double)i / sample_rate;
        double val = sin(2.0 * M_PI * freq_hz * t) * 16000.0;
        buf[i] = (int16_t)val;
    }
}

static void beep_task(void *arg)
{
    beep_params_t params = *(beep_params_t *)arg;
    free(arg);

    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = BITS_PER_SAMPLE,
        .channel = 1,
        .channel_mask = 0,
        .sample_rate = SAMPLE_RATE,
        .mclk_multiple = MCLK_MULTIPLE,
    };

    int ret = esp_codec_dev_open(s_codec_dev, &fs);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "Codec open failed: %d", ret);
        s_playing = false;
        vTaskDelete(NULL);
        return;
    }

    esp_codec_dev_set_out_vol(s_codec_dev, 80);
    i2s_channel_enable(s_i2s_tx);

    int samples_per_chunk = SAMPLE_RATE / 10;
    int16_t *tone_buf = heap_caps_malloc(samples_per_chunk * sizeof(int16_t), MALLOC_CAP_DEFAULT);
    if (tone_buf == NULL) {
        ESP_LOGE(TAG, "Failed to allocate tone buffer");
        goto done;
    }

    generate_tone(tone_buf, samples_per_chunk, params.freq_hz, SAMPLE_RATE);

    for (int r = 0; r < params.repeats; r++) {
        int remaining_ms = params.duration_ms;
        int chunk_ms = (samples_per_chunk * 1000) / SAMPLE_RATE;

        while (remaining_ms > 0) {
            int write_samples = samples_per_chunk;
            if (remaining_ms < chunk_ms) {
                write_samples = (remaining_ms * SAMPLE_RATE) / 1000;
                if (write_samples <= 0) break;
            }
            esp_codec_dev_write(s_codec_dev, tone_buf, write_samples * sizeof(int16_t));
            remaining_ms -= chunk_ms;
        }

        if (r < params.repeats - 1 && params.gap_ms > 0) {
            /* Write silence for the gap */
            int silence_samples = (params.gap_ms * SAMPLE_RATE) / 1000;
            memset(tone_buf, 0, samples_per_chunk * sizeof(int16_t));
            while (silence_samples > 0) {
                int n = silence_samples < samples_per_chunk ? silence_samples : samples_per_chunk;
                esp_codec_dev_write(s_codec_dev, tone_buf, n * sizeof(int16_t));
                silence_samples -= n;
            }
            /* Re-generate tone for next repeat */
            generate_tone(tone_buf, samples_per_chunk, params.freq_hz, SAMPLE_RATE);
        }
    }

    free(tone_buf);

done:
    i2s_channel_disable(s_i2s_tx);
    esp_codec_dev_close(s_codec_dev);
    s_playing = false;
    vTaskDelete(NULL);
}

void audio_beep_play(int freq_hz, int duration_ms, int gap_ms, int repeats)
{
    if (!s_initialized) return;

    xSemaphoreTake(s_play_mutex, portMAX_DELAY);
    if (s_playing) {
        xSemaphoreGive(s_play_mutex);
        ESP_LOGD(TAG, "Beep already playing, ignoring");
        return;
    }
    s_playing = true;
    xSemaphoreGive(s_play_mutex);

    beep_params_t *p = malloc(sizeof(beep_params_t));
    if (p == NULL) {
        s_playing = false;
        return;
    }
    p->freq_hz = freq_hz;
    p->duration_ms = duration_ms;
    p->gap_ms = gap_ms;
    p->repeats = repeats;

    xTaskCreate(beep_task, "beep", 4096, p, 5, NULL);
}

void audio_beep_identify(void)
{
    audio_beep_play(1000, 200, 150, 3);
}
