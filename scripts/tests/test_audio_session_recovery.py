"""Host checks for returning a dead listening/speaking session to idle."""

import os
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class AudioSessionRecoveryTests(unittest.TestCase):
    def test_dead_listening_and_speaking_return_to_idle_without_touching_boot(self):
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("requires a host C++ compiler")
        driver = textwrap.dedent(
            r"""
            #include "audio_session_recovery.h"
            #include <cassert>
            #include <cstdint>

            int main() {
                assert(DecideAudioSessionRecovery(kDeviceStateListening, false) ==
                       AudioSessionRecovery::kReturnToIdle);
                assert(DecideAudioSessionRecovery(kDeviceStateSpeaking, false) ==
                       AudioSessionRecovery::kReturnToIdle);
                assert(DecideAudioSessionRecovery(kDeviceStateListening, true) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateSpeaking, true) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateIdle, false) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateConnecting, false) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateActivating, false) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateWifiConfiguring, false) ==
                       AudioSessionRecovery::kNone);
                assert(DecideAudioSessionRecovery(kDeviceStateUpgrading, false) ==
                       AudioSessionRecovery::kNone);

                constexpr std::uint32_t epoch = 4;
                assert(AudioChannelCloseMayReturnToIdle(epoch, epoch, kDeviceStateListening));
                assert(AudioChannelCloseMayReturnToIdle(epoch, epoch, kDeviceStateSpeaking));
                assert(AudioChannelCloseMayReturnToIdle(epoch, epoch, kDeviceStateConnecting));
                assert(!AudioChannelCloseMayReturnToIdle(epoch, epoch + 1, kDeviceStateConnecting));
                assert(!AudioChannelCloseMayReturnToIdle(epoch, epoch, kDeviceStateIdle));
                assert(!AudioChannelCloseMayReturnToIdle(epoch, epoch, kDeviceStateUpgrading));

                constexpr std::uint32_t generation = 7;
                assert(WebSocketCloseEventIsCurrent(generation, generation));
                assert(!WebSocketCloseEventIsCurrent(generation, generation + 1));
                return 0;
            }
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "audio_session_recovery_test.cc"
            source.write_text(driver, encoding="utf-8")
            executable = path / "audio_session_recovery_test"
            compiled = subprocess.run(
                compiler
                + [
                    "-std=c++23",
                    "-I",
                    str(ROOT / "main"),
                    str(source),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_firmware_uses_the_recovery_rules(self):
        application = (ROOT / "main/application.cc").read_text(encoding="utf-8")
        websocket = (ROOT / "main/protocols/websocket_protocol.cc").read_text(encoding="utf-8")
        self.assertIn("ReturnToIdleIfAudioSessionClosed(\"clock\")", application)
        self.assertIn("ReturnToIdleIfAudioSessionClosed(\"send\")", application)
        self.assertIn("BeginAudioSession()", application)
        self.assertIn("AudioChannelCloseMayReturnToIdle", application)
        close = websocket.split("void WebsocketProtocol::CloseAudioChannel", 1)[1].split(
            "bool WebsocketProtocol::OpenAudioChannel", 1
        )[0]
        reset_at = close.index("websocket_.reset()")
        self.assertLess(close.index("++channel_generation_"), reset_at)
        self.assertIn("WebSocketCloseEventIsCurrent", websocket)
        self.assertIn("abandon()", websocket)
