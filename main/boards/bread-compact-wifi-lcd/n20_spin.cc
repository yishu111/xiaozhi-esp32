#include "n20_spin.h"

#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <mutex>

#include <driver/gpio.h>
#include <driver/ledc.h>
#include <esp_err.h>
#include <esp_log.h>
#include <esp_ota_ops.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "config.h"

namespace {

constexpr const char* TAG = "N20Spin";
constexpr uint32_t kLedcFullScale = 1023;
constexpr uint32_t kLedcDutyCap = (kLedcFullScale * n20::kMaxDutyPercent) / 100;

uint32_t DutyTicks(int duty_percent) {
    if (duty_percent <= 0) {
        return 0;
    }
    if (duty_percent > n20::kMaxDutyPercent) {
        duty_percent = n20::kMaxDutyPercent;
    }
    uint32_t ticks = (kLedcFullScale * static_cast<uint32_t>(duty_percent)) / 100;
    if (ticks > kLedcDutyCap) {
        ticks = kLedcDutyCap;
    }
    return ticks;
}

class N20Spin {
public:
    N20Spin() {
        ready_ = ConfigureIo();
        LogRunningApp();
        if (ready_) {
            ready_ = CreateTimers();
        }
        ESP_LOGI(TAG, "idle stby=0 duty=0; commands are 'motor start' and 'motor stop'");
        if (!ready_) {
            ESP_LOGE(TAG, "motor io unavailable");
        }
        if (xTaskCreate(ConsoleTask, "n20_spin", 4096, this, tskIDLE_PRIORITY + 1, nullptr) !=
            pdPASS) {
            ESP_LOGE(TAG, "console task failed");
        }
    }

private:
    static void ConsoleTask(void* arg) { static_cast<N20Spin*>(arg)->ConsoleLoop(); }

    static void RampThunk(void* arg) { static_cast<N20Spin*>(arg)->OnRamp(); }

    static void DeadlineThunk(void* arg) { static_cast<N20Spin*>(arg)->OnDeadline(); }

    static int64_t NowMs() { return esp_timer_get_time() / 1000; }

    bool ConfigureIo() {
        // STBY first, while the bridge is still held down by its own pull-down.
        if (!DriveLow(MOTOR_STBY_GPIO)) {
            return false;
        }
        if (!DriveLow(MOTOR_AIN1_GPIO) || !DriveLow(MOTOR_AIN2_GPIO) ||
            !DriveLow(MOTOR_PWMA_GPIO)) {
            DriveLow(MOTOR_STBY_GPIO);
            return false;
        }

        ledc_timer_config_t timer = {};
        timer.speed_mode = LEDC_LOW_SPEED_MODE;
        timer.duty_resolution = LEDC_TIMER_10_BIT;
        timer.timer_num = LEDC_TIMER_1;
        timer.freq_hz = n20::kPwmHz;
        timer.clk_cfg = LEDC_AUTO_CLK;
        esp_err_t err = ledc_timer_config(&timer);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ledc timer failed: %s", esp_err_to_name(err));
            return false;
        }

        ledc_channel_config_t channel = {};
        channel.gpio_num = MOTOR_PWMA_GPIO;
        channel.speed_mode = LEDC_LOW_SPEED_MODE;
        channel.channel = LEDC_CHANNEL_1;
        channel.intr_type = LEDC_INTR_DISABLE;
        channel.timer_sel = LEDC_TIMER_1;
        channel.duty = 0;
        channel.hpoint = 0;
        err = ledc_channel_config(&channel);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ledc channel failed: %s", esp_err_to_name(err));
            DriveLow(MOTOR_STBY_GPIO);
            return false;
        }
        ApplyOutput(n20::Output{});
        return true;
    }

    bool DriveLow(gpio_num_t pin) {
        gpio_reset_pin(pin);
        esp_err_t err = gpio_set_pull_mode(pin, GPIO_PULLDOWN_ONLY);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "gpio pull failed: %s", esp_err_to_name(err));
            return false;
        }
        // Latch a low level before the pin becomes an output.
        gpio_set_level(pin, 0);
        err = gpio_set_direction(pin, GPIO_MODE_OUTPUT);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "gpio direction failed: %s", esp_err_to_name(err));
            return false;
        }
        gpio_set_level(pin, 0);
        return true;
    }

    bool CreateTimers() {
        esp_timer_create_args_t ramp_args = {};
        ramp_args.callback = RampThunk;
        ramp_args.arg = this;
        ramp_args.dispatch_method = ESP_TIMER_TASK;
        ramp_args.name = "n20_ramp";
        ramp_args.skip_unhandled_events = true;
        esp_err_t err = esp_timer_create(&ramp_args, &ramp_timer_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "timer init failed: %s", esp_err_to_name(err));
            return false;
        }

        esp_timer_create_args_t deadline_args = {};
        deadline_args.callback = DeadlineThunk;
        deadline_args.arg = this;
        deadline_args.dispatch_method = ESP_TIMER_TASK;
        deadline_args.name = "n20_deadline";
        deadline_args.skip_unhandled_events = false;
        err = esp_timer_create(&deadline_args, &deadline_timer_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "timer init failed: %s", esp_err_to_name(err));
            return false;
        }
        return true;
    }

    void LogRunningApp() {
        const esp_partition_t* running = esp_ota_get_running_partition();
        if (running == nullptr) {
            return;
        }
        ESP_LOGI(TAG, "running app %s at 0x%08" PRIx32, running->label, running->address);
    }

    void ApplyOutput(const n20::Output& raw) {
        n20::Output out = n20::Sanitize(raw);
        if (!out.stby) {
            gpio_set_level(MOTOR_STBY_GPIO, 0);
            ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, 0);
            ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
            gpio_set_level(MOTOR_AIN1_GPIO, 0);
            gpio_set_level(MOTOR_AIN2_GPIO, 0);
            return;
        }
        gpio_set_level(MOTOR_AIN1_GPIO, 1);
        gpio_set_level(MOTOR_AIN2_GPIO, 0);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, DutyTicks(out.duty_percent));
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
        gpio_set_level(MOTOR_STBY_GPIO, 1);
    }

    bool ArmTimers() {
        esp_timer_stop(deadline_timer_);
        esp_timer_stop(ramp_timer_);
        esp_err_t err =
            esp_timer_start_once(deadline_timer_, static_cast<uint64_t>(n20::kMaxRunMs) * 1000);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "timer arm failed: %s", esp_err_to_name(err));
            return false;
        }
        err = esp_timer_start_periodic(ramp_timer_, static_cast<uint64_t>(n20::kTickMs) * 1000);
        if (err != ESP_OK) {
            esp_timer_stop(deadline_timer_);
            ESP_LOGE(TAG, "timer arm failed: %s", esp_err_to_name(err));
            return false;
        }
        return true;
    }

    void DisarmTimers() {
        if (deadline_timer_ != nullptr) {
            esp_timer_stop(deadline_timer_);
        }
        if (ramp_timer_ != nullptr) {
            esp_timer_stop(ramp_timer_);
        }
    }

    void OnRamp() {
        bool timed_out = false;
        {
            std::lock_guard<std::mutex> lock(mu_);
            ApplyOutput(policy_.OutputNow(NowMs()));
            timed_out = policy_.ConsumeTimeout();
            if (timed_out) {
                esp_timer_stop(deadline_timer_);
            }
        }
        if (timed_out) {
            esp_timer_stop(ramp_timer_);
            ESP_LOGI(TAG, "timeout stop");
        }
    }

    void OnDeadline() {
        bool timed_out = false;
        {
            std::lock_guard<std::mutex> lock(mu_);
            policy_.NoteDeadline();
            timed_out = policy_.ConsumeTimeout();
            ApplyOutput(policy_.OutputNow(NowMs()));
            if (timed_out) {
                esp_timer_stop(ramp_timer_);
            }
        }
        if (timed_out) {
            ESP_LOGI(TAG, "timeout stop");
        }
    }

    void Handle(n20::LineAction action) {
        const char* log = nullptr;
        bool warn = false;
        {
            std::lock_guard<std::mutex> lock(mu_);
            if (action == n20::LineAction::kStart) {
                if (!ready_) {
                    log = "start refused: motor io unavailable";
                    warn = true;
                } else if (policy_.running()) {
                    log = "busy deadline unchanged";
                } else if (!ArmTimers()) {
                    log = "start refused: timer arm failed";
                    warn = true;
                } else {
                    policy_.Apply(n20::LineAction::kStart, NowMs());
                    ApplyOutput(policy_.OutputNow(NowMs()));
                    log = "start";
                }
            } else if (action == n20::LineAction::kStop) {
                DisarmTimers();
                policy_.Apply(n20::LineAction::kStop, NowMs());
                ApplyOutput(n20::Output{});
                log = "stop";
            } else if (action == n20::LineAction::kReject) {
                log = "rejected";
                warn = true;
            }
        }
        if (log == nullptr) {
            return;
        }
        if (warn) {
            ESP_LOGW(TAG, "%s", log);
        } else if (strcmp(log, "start") == 0) {
            ESP_LOGI(TAG, "start duty_max=%d run_ms=%d", n20::kMaxDutyPercent, n20::kMaxRunMs);
        } else {
            ESP_LOGI(TAG, "%s", log);
        }
    }

    void DrainPendingInput() {
        int fd = fileno(stdin);
        if (fd < 0) {
            return;
        }
        int flags = fcntl(fd, F_GETFL, 0);
        if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
            return;
        }
        while (fgetc(stdin) != EOF) {
        }
        clearerr(stdin);
        fcntl(fd, F_SETFL, flags);
    }

    void ConsoleLoop() {
        setvbuf(stdin, nullptr, _IONBF, 0);
        DrainPendingInput();
        n20::LineBuffer line;
        while (true) {
            int ch = fgetc(stdin);
            if (ch == EOF) {
                clearerr(stdin);
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
            n20::LineAction action = n20::LineAction::kNone;
            if (!line.Push(static_cast<unsigned char>(ch), &action)) {
                continue;
            }
            Handle(action);
        }
    }

    std::mutex mu_;
    n20::SpinPolicy policy_;
    esp_timer_handle_t ramp_timer_ = nullptr;
    esp_timer_handle_t deadline_timer_ = nullptr;
    bool ready_ = false;
};

}  // namespace

void InitializeN20Spin() {
    static N20Spin spin;
    (void)spin;
}
