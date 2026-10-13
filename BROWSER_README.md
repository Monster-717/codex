# 手动验证与浏览器续采

在有桌面浏览器的电脑上运行。云聊天当前没有可交互的浏览器窗口。

```bash
python -m pip install playwright Pillow
python -m playwright install chromium
python browser_dataset.py --url https://newhouse.fang.com/house/s/ --target 200 --output floorplan_dataset --profile .fang-browser-profile
```

Windows 如果没有 `python` 命令，可以将其替换为 `py`。

出现验证时，脚本在终端暂停。在打开的浏览器窗口中手动完成网站验证，然后返回终端按回车。不要关闭浏览器窗口。脚本继续使用同一个浏览器上下文和会话，读取户型详情页的原图地址并下载。没有自动验证码识别或规避逻辑。

默认每次请求间隔五秒。页面超时或服务器暂时错误会有限退避重试。登录或验证仍可能反复出现；浏览器方式并不保证网站允许批量访问。

`--profile` 目录保存私人会话，不要发送给别人或加入 Git。压缩包排除此目录。`--output` 目录保存原图、等比例补白的 1024×1024 图片和 manifest.json。包内已有的 16 张图片会被保留并计入 200 张目标；脚本按像素内容去重。按 Ctrl+C 会保存数据并生成现有数据的 ZIP，重新执行同一命令续采。

成功达到目标时返回 0；数量不足返回 1；中断返回 130。最终 ZIP 为 `floorplan_dataset.zip`，其中图片和来源记录才是交付内容。当前真实网站的浏览器批量采集和手动验证尚待本机验证；已通过本地浏览器下载、断点续采、1024尺寸和ZIP完整性检查。
