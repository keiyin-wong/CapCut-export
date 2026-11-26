# CapCut 独立导出器工作记录

更新于 2026-11-26。当前方案是由 Python 读取本地 CapCut 草稿和素材，再通过 FFmpeg 渲染。CapCut 桌面端曾用于在草稿副本里添加效果和查看预览，验证成片全部由 Python 导出器生成。

## 已完成

| 模块 | 当前行为 |
| --- | --- |
| 导出界面 | 按「选草稿 → 导出范围 → 保存为」三步排列。草稿列表自动读取 CapCut 和剪映的默认目录，以及 `globalSetting` 里的自定义草稿目录，按修改时间排序并显示时长。导出范围可选整片或自定义，时间写秒数或 `分:秒`，另有「前 10 秒 / 30 秒 / 1 分钟」快捷按钮，输入有误会即时提示。分辨率、帧率、H.264 / H.265、码率和背景音乐收在「高级设置」里。界面有进度条、取消、播放和打开文件夹，并会记住上次的格式和保存位置。支持 MP4、MOV，以及只导出 MP3、WAV、FLAC 或 AAC。 |
| 分发 | `build.py` 生成免安装文件夹 `dist/CapCut导出器/` 和 87 MiB 的 zip，内含界面 exe、`render.exe`、FFmpeg 和使用说明，对方电脑不需要 Python。FFmpeg 按环境变量 `CAPCUT_EXPORT_FFMPEG`、程序目录下的 `ffmpeg\`、PATH 的顺序查找。编码器先试编码一小段，能用才选，避免没有对应显卡的电脑选到 QSV / NVENC 后失败。草稿从别的电脑拷来、素材路径失效时，会按草稿文件夹名、CapCut / 剪映的 `User Data` 缓存、同名文件的顺序重新定位素材。 |
| 时间线合成 | 按草稿时间和轨道层级合成，视频空档显示黑色。支持静态缩放、位移、旋转、翻转和透明度。 |
| 字幕 | 支持基本样式、位置、时间范围和真实换行。修复竖排字幕被导成含 `n` 的横排文字。 |
| 音频 | 混合视频原声、音效和背景音乐，按片段的时间和音量处理。可读的草稿曲库音乐也参与混音。支持线性音量关键帧和音频淡入淡出，最终只对过高峰值限幅。 |
| 转场 | 支持 Mix 叠化及部分标准转场映射。普通片段优先取剪切点两侧的真实素材帧。通过副本验证了底层视频缩放关键帧与 Mix 同时出现的情况。上层轨道上缩放、移动过的片段也能转场：混合结果带透明度，四周露出下层轨道。带本地 `.cube` LUT 的片段在转场里同样调色。转场窗口内暂停原片段的叠加，由混合结果接管画面。 |
| 变速和倒放 | 支持恒定速度和倒放。无法还原的变速曲线会提示，并以平均速度导出。 |
| 关键帧 | 支持视频位移、缩放、旋转、透明度和音量，以及字幕位移。线性插值已与本机 CapCut 的实现核对。 |
| 贴纸和动画 | 支持本地图像贴纸、已识别的 InfoSticker 图集格式，以及验证过的 `Left` 入场预设。图集按资源声明的帧率播放。 |
| 滤镜和特效 | 实现了基础 Blur、黑白、反色、暗角和可单独解析的本地 `.cube` LUT。Blur 按强度应用到视频层，贴纸和字幕在上方合成。 |

## 已做的验证

- 用《末世 EP01》完成整段导出，验证约 150 秒的画面和音轨。抽查约 20 秒处的竖排字幕，确认换行完整。
- 抽查《Rock Solid》在 1:00–1:05 的响度，草稿要求降低约 13.9 dB，导出实测约 13.8 dB。
- 用真实草稿验证了 2 秒 HEVC MOV 区间导出，以及 4 秒和 5 秒 WAV 区间导出。
- 在 CapCut 里复制《末世 EP01》，在《末世 EP01-copy》中添加了从 100% 到约 165% 的缩放关键帧、编号为 `053` 的图集贴纸、`Left` 入场动画和 Blur。用 Python 导出前 8 秒，确认画面和音轨均为 8 秒。逐帧查看了贴纸显现、背景模糊、字幕以及约 6.67 秒处的 Mix 叠化。 [副本导出验证视频](./末世%20EP01（副本导出验证）.mp4)
- 2026-11-26 改了转场合成后重跑上面的 8 秒导出，并与原验证视频逐帧算 PSNR。0–6.43 秒的 193 帧完全相同，叠化之后的帧约 47 dB，属于编码差异。叠化的 12 帧有变化：原版混合进度在中间停一帧，overlay 又晚取一帧；现在亮度逐帧平滑过渡，没有黑帧。12.4 秒处使用真实素材帧的 133 ms Mix 也重新导出，亮度连续。
- 用 `buildTransitionTestDraft.py` 从副本生成合成草稿，验证上层转场和 LUT 转场。草稿只写到新目录，CapCut 没有打开过它。导出 4.5–8 秒并抽帧：底层 6.67 秒的叠化两侧都带红色 LUT，过渡连续；上层两段缩到 45%、移到右上角的片段在 6.5–7.5 秒叠化，小窗四周是底层画面。改用 130% 放大加偏移（会触发下文的 FFmpeg 缺陷）再导出一次，逐帧亮度连续，混合期间画面完整。这只证明导出器按设计合成，没有与 CapCut 原生预览比对。 [合成验证视频](./末世%20EP01（上层转场与LUT合成验证）.mp4)

- 2026-11-26 做可分发版本时的验证：
  - 现在只预处理导出区间前后 2 秒内的片段，前 8 秒导出从整片预处理改为只处理 2 个片段，用时约 15 秒。与原验证视频相比，画面差异只在叠化帧，音频差值约 -72 dB。
  - 复制草稿并把 20 个素材路径改成另一个用户名下的路径，重新定位后导出结果与原版逐帧相同（PSNR 为 inf）。
  - 用脚本驱动界面：区间解析和报错、导出 1.5–6 秒、取消整片导出（半成品、临时目录和 FFmpeg 进程都被清理）。
  - 解压 zip 到新目录，在只有 `C:\Windows\system32` 的 PATH 下用 `render.exe` 导出 4 秒视频，再以打包模式让界面调用 `render.exe` 导出 10 秒 WAV。双击打包后的 exe，窗口和草稿列表正常。
  - 以上都在本机测试，还没在另一台电脑上试过，尤其是只有 AMD 或 NVIDIA 显卡、没有 Intel 核显的电脑。

## 尚未完成

- CapCut 专属多阶段特效、其他贴纸入场、出场和循环预设、花字与字幕模板、遮罩及复杂调色，没有完整复现。当前的 `Left` 是按这个资源对应的着色器和缓动实现的，不能推及其他名称相似的预设。
- Blur 使用 FFmpeg 高斯模糊近似，加工结果未与 CapCut 原生渲染逐像素比对。部分转场使用 FFmpeg 对应效果，只有实际导出的样本能证明当前工程里的效果。
- 非线性关键帧缓动目前按线性近似。两份本地草稿里只有 `Line` 关键帧，`left_control` / `right_control` 都是 0，没有可用于核对曲线和控制柄单位的样本。下一步需要在 CapCut 副本里给某个关键帧设一个非线性曲线并保存，再读取这两个字段。
- 上层转场和 LUT 转场只用合成草稿验证过。还没在 CapCut 里做一个真实的上层转场样本，与原生预览对比边缘和透明度。
- 多轨叠层、其他关键帧属性、其他贴纸格式及所有导出参数组合尚未用真实草稿逐项验证。当前验证只能覆盖上述样本。
- REA 已确认 CapCut 的内部导出入口、同步与异步分发和会话查找。渲染会话的完整创建过程、内部请求对象的所有字段，以及未识别特效的渲染细节尚未穷尽。当前 Python 导出器没有接入该原生入口。
- 2026-11-26 运行 `rea doctor`：Ghidra、IDA、jadx 都未配置（例如未设置 `GHIDRA_INSTALL_DIR`）。所以 REA 目前只能读 PE 导入导出表，不能反编译函数体，也就看不到缓动曲线、特效着色器参数这类内部算法。要用 REA 分析实现，需要先装 Ghidra 12.2 以上版本并设置该变量。
- 打包版自带的 FFmpeg 是本机的 n4.3.2 静态版，没有 libx264。如果换用带 libx264 的新版 FFmpeg，程序会优先用软件编码。

## 如何用 REA 继续分析

### 启动本机 REA

本机 checkout 位于 `C:\Users\keiyi\Personal\AI\rea`。已用 Node.js 24.22.1 构建并启动 REA 6.10.1。默认 PATH 里的 Node.js 22.23.2 不满足该 checkout 的要求，以下命令明确选择已经安装的运行时。

```powershell
Set-Location 'C:\Users\keiyi\Personal\AI\rea'
$reaNode = Join-Path $env:NVM_HOME 'v24.22.1\node.exe'
& $reaNode scripts/rea.mjs --version
```

若报告缺少 Windows 原生模块，先比对 checkout 与发布标签下的 `native/windows/src` 和 `src/domain/windowsNativeArtifact.ts`。本机恢复时已确认这些文件与 `rea-agents-6.10.1` 一致，随后从 npm 官方 6.10.1 包恢复了 `native/windows/build/manifest.json` 和 `rea-windows-x64.node`，校验了包和模块摘要。源码或契约有差异时，应按该 checkout 自带的 `native/windows/README.md` 使用匹配构建产物。

```powershell
$reaNodeDir = Split-Path $reaNode
$env:Path = "$reaNodeDir;$env:Path"
npm run verify:windows-native:artifact
npm run build:cached
& $reaNode scripts/rea.mjs --version
```

### 只读追踪 CapCut 导出

以下命令读取本机 CapCut 9.8.0.4213 的 PE 导入表，不执行 CapCut DLL。读取上限按目标文件的实际大小设置，避免默认 64 MiB 上限拒绝大型 DLL。`VECreator.dll` 还需要提高导入函数数目的上限。

```powershell
$cap = 'C:\Users\keiyi\AppData\Local\CapCut\Apps\9.8.0.4213'
$engine = Get-Item (Join-Path $cap 'VECreator.dll')
$evidence = & $reaNode scripts/rea.mjs inspect-pe-imports $engine.FullName `
    --max-file-bytes $engine.Length --max-functions 40000 --json | ConvertFrom-Json
$evidence.normalized_result | ConvertTo-Json -Depth 12 |
    Select-String 'exportCompositionToFileSync|prepareExportResources|prepareExportRange|prepareExportCover'

$editor = Get-Item (Join-Path $cap 'videoeditor.dll')
& $reaNode scripts/rea.mjs inspect-pe-imports $editor.FullName `
    --max-file-bytes $editor.Length --json
```

1. 先在 `VECreator.dll` 寻找导出入口和资源准备接口，再在 `videoeditor.dll`、`cccreator.dll` 和 `ffmpeg.dll` 核对轨道、效果、编码及封装相关的导入符号。符号能确认连接关系，不能单独证明运行顺序。
2. 接着用 REA 的 `decompile --help` 查看反编译入口，用 `doctor --target $editor.FullName` 确认分析后端。本机当时缺少可用的 PE 深度分析后端，因此用 Visual Studio Build Tools 的 `dumpbin /exports` 和 `/disasm` 核实了函数体的分支。已观测到 `ExportClient` 把请求转给 `lyra::Server::invokeSync` 或 `invokeAsync`，再按会话 ID 查找 `Session`。
3. 对具体效果，读取副本的 `draft_content.json`，从 `tracks[].segments[].material_id` 找主素材，再通过 `extra_material_refs` 找动画、变速或转场。关键帧在 `common_keyframes`，贴纸预设在 `material_animations`。跟随对应物料的本地 `path` 打开缓存包，分别核对素材图集、帧率、着色器和时间曲线。
4. 只实现已经知道输入和算法的部分。新增一个效果后，先确认 CapCut 已保存副本，再用 Python 导出包含该效果的短区间，抽查关键帧前后或特效进出点。写清验证是看画面、量音量，还是只确认文件成功生成。

### 重跑当前 8 秒验证

```powershell
Set-Location 'C:\Users\keiyi\Personal\AI\capcut-export'
$draft = 'C:\Users\keiyi\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\末世 EP01-copy'
$out = Join-Path (Get-Location) ('capcutValidation-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.mp4')
python -X utf8 -u render.py $draft -o $out --end 8
ffprobe -v error -show_entries stream=codec_type,duration -of compact $out
```

如果 `ffprobe` 不在 PATH，可使用项目里的 `ffmpeg\ffprobe.exe`。核对约 3.0–3.5 秒的贴纸入场、3.5 秒后的背景模糊和约 6.67 秒的叠化。

### 重新打包给别人用

```powershell
Set-Location 'C:\Users\keiyi\Personal\AI\capcut-export'
python -m venv $env:TEMP\ccxBuildEnv
& $env:TEMP\ccxBuildEnv\Scripts\python.exe -m pip install pyinstaller
& $env:TEMP\ccxBuildEnv\Scripts\python.exe -X utf8 build.py    # 默认打包 .\ffmpeg 里的 FFmpeg
```

产物是 `dist\CapCut导出器\` 和 `dist\CapCut导出器.zip`，把 zip 发给对方，解压后双击 `CapCut导出器.exe` 即可。使用说明写在 `使用说明.txt`，会一起打包。

### 重跑上层转场与 LUT 合成验证

```powershell
$copy = 'C:\Users\keiyi\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\末世 EP01-copy'
$test = Join-Path $env:TEMP 'ccxTransitionTest'
python -X utf8 buildTransitionTestDraft.py $copy $test          # 可加 --scale 1.3 --x 0.3 --y 0.25
python -X utf8 -u render.py $test -o transitionTest.mp4 --start 4.5 --end 8
```

核对 6.5–7.5 秒上层小窗的叠化，以及约 6.67 秒底层带红色 LUT 的叠化。测试目录可以直接删除。

### 本机 FFmpeg n4.3.2 的已知问题

转场代码针对下面两个问题做了绕行，修改时不要去掉：

- 底图带 alpha、被叠加画面的纵向偏移为负时，`overlay` 输出的 alpha 会变成 0。因此变换后的转场片段先叠到不透明黑底上，再用同样的变换叠一张白色剪影得到覆盖遮罩，最后用 `alphamerge` 合并。
- `tpad` 复制出的第一帧沿用上一帧的时间戳，会让 `xfade` 的进度停一帧。转场输入和输出都用 `setpts=N/fps` 按帧号重排时间。混合流再整体提前四分之一帧，避免微秒取整导致 overlay 取到前一帧。

