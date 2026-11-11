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

不需要为这张脸生成或烧录 assets。眼睛是运行时画出来的，不要拿 EchoEar / Otto 的 emoji 或 GIF 包当主脸。如果服务器之后下发 assets 下载地址，固件仍会按原逻辑收字体等资源，但这张脸不读那些表情。

显示风格保持工程默认（Default message style）。微信气泡样式的字幕会被眼睛画布挡住。

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
- 待机（`SetEmotion("neutral")`，以及未单独映射的名字）：大约每 1.6–3.4 秒眨一次，闭合曲线按参考预览的 80 ms 步进；眨完会在圆屏里看向新的方向，大约 0.4–1.2 秒换一次。
- `SetEmotion` 映射：
  - `happy` / `laughing` / `laugh` / `funny` / `loving` / `kissy` / `delicious` / `confident` / `cool` / `relaxed` / `embarrassed`：向上的月牙，并带小幅上下晃动。
  - `winking`：同样的月牙，左眼眨一次。
  - `angry`：内角下压的眼皮，小范围张望。
  - `sad` / `crying` / `sleepy` / `tired` / `silly`：上眼皮压低的困倦眼，目光偏下。省电模式会调用 `sleepy`。
  - `surprised` / `shocked`：更大的圆眼，停在中间。
  - `thinking` / `confused`：圆眼看向上方。
  - `neutral` / `robot_2` 以及其他名字：回到待机眼。只要名字非空就会接受（返回 true），因为这块屏没有表情图可回退。
- 聆听、连接中：眼睛回到中间，仍会眨眼。说话：在当前表情上加一点上下跳动。回到待命后再继续四处看。
- 刷新定时器是 66 ms（大约 15 fps）。每帧重画整块 240×240 画布，再经 SPI 送出，实际帧率多半落在 12–20 fps，由 GC9A01 的整帧刷新决定。
- 「待命 / 聆听 / 说话」和闲置时钟不画在脸上。配网、激活码、错误、升级这些 `SetStatus` / 聊天子幕仍显示在圆屏内侧。

没有在实机上点亮这块屏。编译通过只说明固件能编出来，眨眼、张望和颜色要以板上的 GC9A01 为准。
