"""Host checks for the single N20 start/stop policy and the pins it must not take."""

import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/bread-compact-wifi-lcd"
HEADER = BOARD / "n20_spin.h"


@unittest.skipUnless(shutil.which("c++"), "requires a host C++ compiler")
class N20PolicyTests(unittest.TestCase):
    def test_policy_stays_off_and_bounded(self):
        source = textwrap.dedent(
            r"""
            #include "n20_spin.h"
            #include <cassert>
            #include <cstdio>
            #include <cstring>
            #include <string>

            using n20::CommandResult;
            using n20::LineAction;
            using n20::LineBuffer;
            using n20::Output;
            using n20::Sanitize;
            using n20::SpinPolicy;

            LineAction Feed(LineBuffer* line, const char* text) {
                LineAction last = LineAction::kNone;
                bool done = false;
                for (const char* p = text; *p; ++p) {
                    LineAction action = LineAction::kNone;
                    if (!line->Push(static_cast<unsigned char>(*p), &action)) {
                        continue;
                    }
                    last = action;
                    done = true;
                }
                assert(done);
                return last;
            }

            int main() {
                SpinPolicy idle;
                assert(!idle.running());
                assert(!idle.OutputNow(0).stby);
                assert(idle.OutputNow(0).duty_percent == 0);
                assert(!idle.ConsumeTimeout());

                LineBuffer line;
                assert(Feed(&line, "motor start\r") == LineAction::kStart);
                assert(Feed(&line, "\n") == LineAction::kNone);
                {
                    LineBuffer crlf;
                    LineAction actions[2] = {LineAction::kReject, LineAction::kReject};
                    int count = 0;
                    const char* text = "motor start\r\n";
                    for (const char* p = text; *p; ++p) {
                        LineAction action = LineAction::kNone;
                        if (!crlf.Push(static_cast<unsigned char>(*p), &action)) {
                            continue;
                        }
                        assert(count < 2);
                        actions[count++] = action;
                    }
                    assert(count == 2);
                    assert(actions[0] == LineAction::kStart);
                    assert(actions[1] == LineAction::kNone);
                }
                assert(Feed(&line, "motor stop\n") == LineAction::kStop);
                assert(Feed(&line, "motor start \n") == LineAction::kReject);
                assert(Feed(&line, "motor start 100\n") == LineAction::kReject);
                assert(Feed(&line, "MOTOR START\n") == LineAction::kReject);
                assert(Feed(&line, " motor start\n") == LineAction::kReject);

                std::string junk(40, 'A');
                junk.push_back('\n');
                assert(Feed(&line, junk.c_str()) == LineAction::kReject);
                std::string binary = std::string("motor") + '\x01' + "start\n";
                assert(Feed(&line, binary.c_str()) == LineAction::kReject);
                assert(n20::ParseLine(nullptr) == LineAction::kReject);
                assert(n20::ParseLine("") == LineAction::kNone);

                SpinPolicy motor;
                assert(motor.Apply(LineAction::kStart, 0) == CommandResult::kStarted);
                Output at0 = motor.OutputNow(0);
                assert(!at0.stby && at0.duty_percent == 0);
                Output early = motor.OutputNow(6);
                assert(!early.stby && early.duty_percent == 0);
                Output mid = motor.OutputNow(50);
                assert(mid.stby && mid.ain1 && !mid.ain2 && mid.duty_percent == 7);
                Output almost = motor.OutputNow(199);
                assert(almost.duty_percent == 29 && almost.duty_percent <= n20::kMaxDutyPercent);
                Output top = motor.OutputNow(200);
                assert(top.stby && top.duty_percent == n20::kMaxDutyPercent);
                Output hold = motor.OutputNow(799);
                assert(hold.stby && hold.duty_percent == n20::kMaxDutyPercent);
                assert(motor.DeadlineMs() == n20::kMaxRunMs);
                assert(motor.Apply(LineAction::kStart, 400) == CommandResult::kBusy);
                assert(motor.DeadlineMs() == n20::kMaxRunMs);
                assert(motor.Apply(LineAction::kReject, 400) == CommandResult::kRejected);
                assert(motor.running());
                Output ended = motor.OutputNow(800);
                assert(!ended.stby && ended.duty_percent == 0);
                assert(!motor.running());
                assert(motor.ConsumeTimeout());
                assert(!motor.ConsumeTimeout());
                assert(!motor.OutputNow(5000).stby);

                assert(motor.Apply(LineAction::kStart, 1000) == CommandResult::kStarted);
                assert(motor.DeadlineMs() == 1800);
                assert(motor.Apply(LineAction::kStart, 1200) == CommandResult::kBusy);
                assert(motor.DeadlineMs() == 1800);
                assert(motor.OutputNow(1799).stby);
                motor.NoteDeadline();
                assert(!motor.running());
                assert(!motor.OutputNow(1200).stby);
                assert(motor.ConsumeTimeout());
                motor.NoteDeadline();
                assert(!motor.ConsumeTimeout());

                assert(motor.Apply(LineAction::kStart, 0) == CommandResult::kStarted);
                assert(motor.Apply(LineAction::kStop, 100) == CommandResult::kStopped);
                assert(!motor.OutputNow(100).stby);
                assert(!motor.ConsumeTimeout());
                motor.NoteDeadline();
                assert(!motor.ConsumeTimeout());

                assert(motor.Apply(LineAction::kStart, 100) == CommandResult::kStarted);
                assert(!motor.OutputNow(50).stby);
                assert(motor.ConsumeTimeout());

                assert(motor.Apply(LineAction::kStart, 0) == CommandResult::kStarted);
                assert(motor.Apply(LineAction::kStop, 800) == CommandResult::kStopped);
                assert(!motor.ConsumeTimeout());

                Output too_high;
                too_high.stby = true;
                too_high.ain1 = true;
                too_high.duty_percent = 100;
                Output clamped = Sanitize(too_high);
                assert(clamped.stby && clamped.duty_percent == n20::kMaxDutyPercent);

                Output reverse = clamped;
                reverse.ain1 = false;
                reverse.ain2 = true;
                assert(!Sanitize(reverse).stby);
                Output both = clamped;
                both.ain2 = true;
                assert(!Sanitize(both).stby);
                Output coast = clamped;
                coast.stby = false;
                assert(!Sanitize(coast).stby);
                Output zero = clamped;
                zero.duty_percent = 0;
                assert(!Sanitize(zero).stby);
                Output negative = clamped;
                negative.duty_percent = -5;
                assert(!Sanitize(negative).stby);

                assert(n20::kMaxDutyPercent <= 30);
                assert(n20::kMaxRunMs <= 800);
                assert(n20::kRampMs < n20::kMaxRunMs);
                std::puts("ok");
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "n20_policy_test.cc"
            path.write_text(source)
            binary = pathlib.Path(tmp) / "n20_policy_test"
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            compile_cmd = compiler + [
                "-std=c++23",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(BOARD),
                str(path),
                "-o",
                str(binary),
            ]
            subprocess.check_call(compile_cmd)
            subprocess.check_call([str(binary)])


class N20SourceTests(unittest.TestCase):
    def test_pins_do_not_take_existing_or_reserved_gpio(self):
        config = (BOARD / "config.h").read_text()
        start = config.index("#ifdef CONFIG_LCD_GC9A01_240X240")
        end = config.index("#endif", start)
        gc9a01 = config[start:end]
        pins = {
            "MOTOR_AIN1_GPIO": 8,
            "MOTOR_AIN2_GPIO": 9,
            "MOTOR_PWMA_GPIO": 10,
            "MOTOR_STBY_GPIO": 42,
        }
        for name, number in pins.items():
            self.assertIn(f"#define {name} GPIO_NUM_{number}", gc9a01)
        self.assertIn("#define DISPLAY_BACKLIGHT_PIN GPIO_NUM_NC", gc9a01)
        self.assertNotIn("GPIO_NUM_46", config)
        self.assertIn("#define LAMP_GPIO GPIO_NUM_18", config)

        # GPIO42 was the unconnected backlight PWM. The 7-pin panel lights from VCC.
        still_used = {
            0, 4, 5, 6, 7, 15, 16, 18, 19, 20, 21, 35, 36, 37, 40, 41, 43, 44, 45, 47, 48
        }
        reserved = {1, 2, 3, 11, 12, 13, 14, 17, 38, 39, 46}
        motor = {8, 9, 10, 42}
        self.assertTrue(motor.isdisjoint(still_used))
        self.assertTrue(motor.isdisjoint(reserved))

    def test_output_gpio_mask_accepts_motor_pins(self):
        idf = os.environ.get("IDF_PATH", "")
        header = pathlib.Path(idf) / "components/soc/esp32s3/include/soc/soc_caps.h" if idf else None
        if header is None or not header.is_file():
            self.skipTest("set IDF_PATH to the ESP-IDF tree to check SOC_GPIO_VALID_OUTPUT_GPIO_MASK")
        text = header.read_text()
        self.assertIn("SOC_GPIO_VALID_OUTPUT_GPIO_MASK", text)
        source = r"""
            #include <stdio.h>
            #define BIT(n) (1ULL << (n))
            #define BIT22 BIT(22)
            #define BIT23 BIT(23)
            #define BIT24 BIT(24)
            #define BIT25 BIT(25)
            #include "soc/soc_caps.h"
            int main(void) {
                const int pins[] = {8, 9, 10, 42};
                for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
                    unsigned long long bit = 1ULL << pins[i];
                    if ((SOC_GPIO_VALID_OUTPUT_GPIO_MASK & bit) == 0) {
                        printf("reject %d\n", pins[i]);
                        return 1;
                    }
                }
                printf("mask=0x%llx\n", (unsigned long long)SOC_GPIO_VALID_OUTPUT_GPIO_MASK);
                return 0;
            }
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gpio_mask.c"
            path.write_text(textwrap.dedent(source))
            binary = pathlib.Path(tmp) / "gpio_mask"
            subprocess.check_call(
                [
                    "cc",
                    "-Wall",
                    "-Werror",
                    "-I",
                    str(header.parents[1]),
                    str(path),
                    "-o",
                    str(binary),
                ]
            )
            subprocess.check_call([str(binary)])

    def test_board_keeps_lamp_and_does_not_register_motor_mcp(self):
        board = (BOARD / "compact_wifi_board_lcd.cc").read_text()
        driver = (BOARD / "n20_spin.cc").read_text()
        self.assertIn("LampController lamp(LAMP_GPIO)", board)
        self.assertIn("InitializeN20Spin()", board)
        self.assertNotIn("AddTool", driver)
        self.assertNotIn("AddUserOnlyTool", driver)
        self.assertIn("LEDC_TIMER_1", driver)
        self.assertIn("LEDC_CHANNEL_1", driver)
        self.assertNotIn("LEDC_TIMER_0", driver)
        self.assertNotIn("LEDC_CHANNEL_0", driver)

    def test_existing_wake_eyes_and_ota_url_stay(self):
        config = (BOARD / "config.json").read_text()
        self.assertIn('http://192.168.31.73:8003/xiaozhi/ota/', config)
        wake = (BOARD / "gc9a01_wake_commands.json").read_text()
        self.assertIn('"threshold": 0.2', wake)
        self.assertIn("hi TdKm", wake)
        self.assertIn("hd TdKm", wake)
        face = (BOARD / "roboeyes_face.cc").read_text()
        self.assertIn("PickWanderTarget", face)
        engine = (ROOT / "main/audio/engines/afe_audio_engine.cc").read_text()
        self.assertIn("ESP_WN_PREFIX", engine)
        self.assertIn("ESP_MN_PREFIX", engine)
        self.assertIn("custom_wake_word_", engine)
        partition = (ROOT / "partitions/v2/16m.csv").read_text()
        self.assertIn("ota_0,    app,  ota_0,   0x20000,   0x3f0000,", partition)
        self.assertIn("assets,   data, spiffs,  0x800000,  8M", partition)
