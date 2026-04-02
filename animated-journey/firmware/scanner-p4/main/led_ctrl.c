#include "led_ctrl.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "led_ctrl";

static int s_gpio = -1;
static bool s_state = false;
static int64_t s_identify_end_us = 0;
static int64_t s_last_toggle_us = 0;

#define HEARTBEAT_PERIOD_US   (2000000)
#define HEARTBEAT_ON_US       (100000)
#define IDENTIFY_PERIOD_US    (150000)
#define IDENTIFY_DURATION_US  (10000000)

void led_ctrl_init(int gpio_num)
{
    s_gpio = gpio_num;
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << gpio_num,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    gpio_set_level(gpio_num, 0);
    ESP_LOGI(TAG, "Status LED on GPIO%d", gpio_num);
}

void led_ctrl_set(bool on)
{
    if (s_gpio < 0) return;
    s_state = on;
    gpio_set_level(s_gpio, on ? 1 : 0);
}

void led_ctrl_identify(void)
{
    s_identify_end_us = esp_timer_get_time() + IDENTIFY_DURATION_US;
    s_last_toggle_us = 0;
    ESP_LOGI(TAG, "Identify triggered (10s rapid blink)");
}

bool led_ctrl_is_identifying(void)
{
    return esp_timer_get_time() < s_identify_end_us;
}

void led_ctrl_tick(void)
{
    if (s_gpio < 0) return;

    int64_t now = esp_timer_get_time();

    if (now < s_identify_end_us) {
        if (now - s_last_toggle_us >= IDENTIFY_PERIOD_US) {
            s_state = !s_state;
            gpio_set_level(s_gpio, s_state ? 1 : 0);
            s_last_toggle_us = now;
        }
        return;
    }

    int64_t phase = now % HEARTBEAT_PERIOD_US;
    bool want = (phase < HEARTBEAT_ON_US);
    if (want != s_state) {
        s_state = want;
        gpio_set_level(s_gpio, want ? 1 : 0);
    }
}
