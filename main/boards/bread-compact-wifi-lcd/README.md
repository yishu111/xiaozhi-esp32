# 面包板 Wi-Fi + LCD（GC9A01 圆屏 RoboEyes 脸）

这块板子有两套固件名字，引脚相同：

| 变体名 | 屏幕 | 表情 |
| --- | --- | --- |
| `bread-compact-wifi-lcd` | ST7789 240×320 | 原来的表情 UI |
| `bread-compact-wifi-lcd-gc9a01` | GC9A01 240×240 圆屏 | 单屏 RoboEyes 风格脸 |

圆屏本身就是脸，没有第二块屏。麦克风和喇叭仍走原来的单工 I2S 引脚，没有改。

## 引脚（与官方 bread-compact-wifi-lcd 相同）

- INMP441：WS GPIO4，SCK GPIO5，DIN GPIO6
- MAX98357：DOUT GPIO7，BCLK GPIO15，LRCK GPIO16
- GC9A01：背光 GPIO42，MOSI GPIO47，CLK GPIO21，DC GPIO40，RST GPIO45，CS GPIO41
- BOOT GPIO0，板载 LED GPIO48

GC9A01 参数仍是 `config.h` 里的 240×240、`MIRROR_X`、颜色反转、BGR、SPI mode 0。

## 编译

使用 ESP-IDF `release/v6.1`（最低 v6.0.1）。不要用 IDF 5.x。

```sh
source /path/to/esp-idf/export.sh
cd /path/to/xiaozhi-esp32
python3 scripts/build.py bread-compact-wifi-lcd --name bread-compact-wifi-lcd-gc9a01
```

等价的 menuconfig（目标芯片先 `idf.py set-target esp32s3`）：

```
Xiaozhi Assistant
  -> Board Type
     -> Bread Compact Wi-Fi + LCD (面包板)
  -> LCD Type
     -> GC9A01 240*240 Circle
```

然后 `idf.py build`。选中 GC9A01 后，这块板会用 RoboEyes 脸，不再铺官方表情图。LVGL 的 Canvas 需要开着（`CONFIG_LV_USE_CANVAS`，组件默认就是开的；上面的变体也会写上这一项）。

眼睛仍是运行时画出来的，不要拿 EchoEar / Otto 的 emoji 或 GIF 包当主脸。唤醒模型不在脸的画布里：这一变体默认把中文 MultiNet7 打进 assets 分区，所以要保留 `CONFIG_FLASH_DEFAULT_ASSETS=y`（非 Emote 风格的默认值就是 y），并把生成的 assets 一起烧录。界面语言仍用 `--language`，不写进板级配置；中文界面是 `--language zh-CN`。

显示风格保持工程默认（Default message style）。微信气泡样式的字幕会被眼睛画布挡住。

## 唤醒词（只这一变体）

`bread-compact-wifi-lcd`（ST7789）仍走原来的 WakeNet，默认中文是「你好小智」。只有 `bread-compact-wifi-lcd-gc9a01` 改成离线自定义唤醒：`CONFIG_USE_CUSTOM_WAKE_WORD=y`，`CONFIG_USE_AFE_WAKE_WORD` 关闭，中文模型 `CONFIG_SR_MN_CN_MULTINET7_QUANT`（`mn7_cn`）。不打开英文 MultiNet。

命令词不能中英混写，也不能把英文字母写进 `command`。Taky 用近音「特基」（`te ji`），「hi」用「嗨」（`hai`）。这是 MultiNet7 `vocab` 里的标准音节；`tei` 不在词表里。三条都会 `action=wake`，阈值是设备端 `0.2`（Kconfig 的 20），`duration` 仍是 3000 ms：

| 说法 | command | 屏幕 / 上报文本 |
| --- | --- | --- |
| 你好 Taky | `ni hao te ji` | 你好Taky |
| hi Taky / hi taky | `hai te ji` | hi Taky |
| 你好小智（第一次拼音不稳时的回退） | `ni hao xiao zhi` | 你好小智 |

Kconfig 只有一条 `CUSTOM_WAKE_WORD`，写的是第一条，给没有 assets 模型表时的回退。设备从 assets 启动时，`CustomWakeWord::Initialize` 只读 `index.json` 的 `multinet_model.commands`。这三条在 `gc9a01_wake_commands.json`，由 `scripts/build_default_assets.py --wake_commands` 写进 `index.json`。CMake 只在「面包板 Wi-Fi + LCD」并且 LCD 是 GC9A01 时传入这个文件，其它板型仍是原来的单词。

启动提示走 `GetWakeWords()`，三条显示文本都会列出来，中文界面是：说「你好Taky / hi Taky / 你好小智」就能唤醒我。

分区表仍是 `partitions/v2/16m.csv`：`ota_0` 在 `0x20000`、大小 `0x3f0000`，`assets` 在 `0x800000`、大小 8 MiB（8388608 字节）。不要改用 `16m_custom_wakeword.csv`。用 ESP-SR 2.4.7 打出来的 `srmodels.bin` 是 2681351 字节：`mn7_cn` 四个文件（`_MODEL_INFO_`、`vocab`、`mn7_index`、`mn7_data`）加上 MultiNet7 会一起拷贝的 `fst` 目录（`commands_cn.txt` 7298 字节、`commands_en.txt` 2350 字节）。`fst` 里的英文命令文本不是 `mn7_en` 声学模型，sdkconfig 里英文模型保持 `CONFIG_SR_MN_EN_NONE=y`。只有模型时的 `assets.bin` 是 2682075 字节。再加上这块板原来的 `font_noto_sans_common_16_4.bin`（885948 字节）和 21 张 32px 表情（34366 字节），大约 3602389 字节，低于 8 MiB。

显式 `--wake-word nihaoxiaozhi` 或 `hiesp` 仍可临时改回 WakeNet。不传这个参数时，本变体固定是上面的自定义唤醒。

产物在 `build/`。本仓库的构建说明到此为止，不要在没有确认 bootloader 的情况下把镜像写进 XMC 闪存的板子。

## XMC 闪存（制造商 ID 0x70）

ESP32-S3 N16R8 上常见一颗 XMC 闪存，JEDEC ID 形如 `0x207018`：厂商字节 `0x20`，MFID `0x70`，CPID `0x18`。上游 ESP-IDF bootloader 的 `is_xmc_chip_strict()` 只认一部分 XMC ID。MFID `0x70` 不在名单里时，启动会打印：

```text
E (...) bootloader_flash: XMC flash startup fail
E (...) boot.esp32s3: failed when running XMC startup flow, reboot!
```

然后不断重启。应用分区本身改不了这件事，必须用打过白名单的 bootloader 重新编出完整镜像。

在用来编译的那份 ESP-IDF（不要改本仓库）里，编辑：

`components/bootloader_support/bootloader_flash/src/bootloader_flash.c`

在 `is_xmc_chip_strict()` 现有的 `mfid == 0x40 / 0x41 / 0x50` 分支后面加上：

```c
else if (mfid == 0x70) {
    if (cpid >= 0x17 && cpid <= 0x20) {
        matched = true;
    }
}
```

保持 `CONFIG_BOOTLOADER_FLASH_XMC_SUPPORT=y`（IDF 默认就是打开的）。改完后在本项目里 `idf.py fullclean`，再按上面的命令重编，这样 `bootloader.bin` 和 `merged-binary.bin` 才带这个白名单。这是兼容性补丁，不是乐鑫对这颗料的正式支持；烧录后确认能过启动，并抽查闪存读写。

## 屏幕上会看到什么

- 黑底、两只青色眼睛（约 `#78E6FF`），直径约 69 像素，位置按 240×240 参考图对齐。
- 待机和聆听（`SetStatus` 为待命或聆听，情绪是 `neutral` 或其他未单独锁定目光的名字）：大约每 1.6–3.4 秒眨一次；两只眼睛一起在圆屏里换方向，幅度与参考 GIF 同级（大约几十像素），一次扫视大约 280 ms，停 0.4–1.2 秒再换。自动对话几乎一直停在聆听，所以张望发生在聆听，而不只是长时间待命。
- `SetEmotion` 映射：
  - `happy` / `laughing` / `laugh` / `funny` / `loving` / `kissy` / `delicious` / `confident` / `cool` / `relaxed` / `embarrassed`：向上的月牙，并带小幅上下晃动。聆听时仍然四处看。
  - `winking`：同样的月牙，左眼眨一次。
  - `angry`：内角下压的眼皮，小范围张望。
  - `sad` / `crying` / `sleepy` / `tired` / `silly`：上眼皮压低的困倦眼，目光偏下。省电模式会调用 `sleepy`。
  - `surprised` / `shocked`：更大的圆眼，仍会跟着当前状态张望。
  - `thinking` / `confused`：圆眼看向上方，暂停扫视。
  - `neutral` / `robot_2` 以及其他名字：回到待机眼。只要名字非空就会接受（返回 true），因为这块屏没有表情图可回退。
- 说话：幅度收小的扫视，再加一点上下跳动，避免长答复时钉在正中。连接中短暂回到中间。
- 刷新定时器是 66 ms（大约 15 fps）。每帧重画整块 240×240 画布，再经 SPI 送出，实际帧率多半落在 12–20 fps，由 GC9A01 的整帧刷新决定。
- 「待命 / 聆听 / 说话」和闲置时钟不画在脸上。配网、激活码、错误、升级这些 `SetStatus` / 聊天子幕仍显示在圆屏内侧。

没有在实机上点亮这块屏。编译通过只说明固件能编出来，眨眼、张望和颜色要以板上的 GC9A01 为准。
