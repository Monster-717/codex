# 房天下户型图采集

## 下载与浏览器续采

下载 [浏览器续采包](downloads/fang_browser_resume.zip)，解压后按照包内 BROWSER_README.md 操作。该包包含采集脚本和当前已收集的 16 张图片，尚未达到 200 张目标。下载和使用图片需遵守来源网站规则及图片许可。

浏览器手动验证方案参见 [BROWSER_README.md](BROWSER_README.md)。无需分享浏览器会话或登录凭据。

需要 Python 3 和 curl，无第三方 Python 依赖。

```bash
python crawl_floorplans.py --url 'https://newhouse.fang.com/loupan/1010203883.htm' --output /workspace/floorplans --max-pages 1
```

也可以传入 `https://newhouse.fang.com/house/s/`，通过 `--max-pages` 限制列表页和楼盘页的访问总数。全国首页不包含户型图，请使用新房列表页或楼盘详情页。

脚本识别指向 `photo/d_house_*.htm` 的图片，保存页面实际提供的缩略图，不推测原图地址。`manifest.json` 记录来源、描述、成功文件和失败原因。图片内容以 SHA256 去重。默认每次请求间隔两秒，不跟随重定向，不处理登录或验证码，失败时返回非零状态。其他网站需要适配。

公开可访问不等于允许任意使用，请遵守网站规则和图片使用许可。当前已实测从示例详情页识别并成功下载五张户型缩略图，来源记录见输出目录的 manifest.json。户型相册曾跳转到验证页，批量采集稳定性尚未验证。原图模式已实测成功下载五张图片。官方接口文档请求返回未登录，尚未确认存在可用的户型图 API。

## 下载原图

添加 `--original`，脚本会访问各户型详情页，读取网站提供的 `data-imgSource` 原图地址，而非猜测缩略图 URL。详情页验证跳转或无原图地址时会记录失败，不自动降级为缩略图。

```bash
python crawl_floorplans.py --url https://newhouse.fang.com/loupan/1010203883.htm --output /workspace/floorplans/originals --max-pages 1 --original
```

## 批量采集与自动重试

需要 Pillow。采集目标为 200–500 张，每张同时保存原图和等比例补白的 1024×1024 JPEG。manifest.json 保留来源，按解码后的像素内容去重。输出包含 ZIP。遇到连续页面失败则停止当前轮；自动重试入口在退避后继续，达到目标才返回成功。重试用尽仍不足时返回非零，已有数据不会丢失。网站验证码不会自动处理。

```bash
python retry_dataset.py --url https://newhouse.fang.com/house/s/ --target 200 --output /workspace/floorplan_dataset --attempts 3 --backoff 60
```
