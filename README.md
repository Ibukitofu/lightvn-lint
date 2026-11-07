# lightvn-lint

> Light.vn 引擎脚本静态校验器。只读比对原版与汉化版脚本，找出会在游戏里暴露的破坏比如：贴图丢失、跳转黑屏、分支不触发等问题。

已在 3 个 Light.vn 游戏上验证过

| 游戏 | 引擎版本 |
|---|---|
| [ユニゾンコード](https://www.dlsite.com/maniax/work/=/product_id/RJ01407417.html) | 16.9.2 |
| [恋愛移植](https://www.freem.ne.jp/win/game/33177) | 16.4.1 |
| [コンビニ珍道中](https://www.freem.ne.jp/win/game/32869) | 16.2.1 |

## 规则

| 规则 | 检查 | 破坏了会怎样 |
|---|---|---|
| R1 | 资源路径，以及拼进路径的变量取值 | 贴图、音乐不出来 |
| R2 | 跳转目标（`ジャンプ` / `スクリプト` / `栞`） | 黑屏、卡死 |
| R3 | 变量引用（改名、用了没赋值的） | 数值、状态异常 |
| R4 | 命令语法（命令名被翻译、必填参数缺失） | 该行静默失效 |
| R5 | 逻辑键（赋值侧和比较侧对不上） | 分支永远不成立 |
| R6 | 引号外的代码骨架 | 结构损坏 |
| R7 | 两侧相同但仍是日文 | 玩家直接看到日文 |

## 要求

- Python 3.8+

## 用法

### 预处理

工具不处理 `.mcdat` 容器，只读纯文本脚本。

先用以下的解包器导出，得到一串 `<id>.txt`：

- [Light.vnTools](https://github.com/bungaku-moe/Light.vnTools) — 解包 + 重打包
- [Light.VNRev](https://github.com/shiikwi/Light.VNRev) — 解包 + 重打包 + 生成补丁
- [devndat](https://github.com/Konoyoru435/devndat) — 只做解密

### 校验汉化版

`LV_JP` 指向原版解包出的纯文本目录。

```bash
LV_GAME=/path/to/build LV_JP=/path/to/jp_plaintext python linter.py
```

### 校验未改动的原版

不给 `LV_JP` 时，工具会解开 `LV_GAME` 自己当基线。这是检验规则是否过严的方式，应当输出 0 条 `translation`。

```bash
LV_GAME=/path/to/original python linter.py
```

### 只跑部分规则

```bash
python linter.py --only R1,R2
```

### 多游戏批量

`name=游戏目录|日文目录`，日文目录可省。结果写到 `out3/<name>/`。

```bash
python run_all3.py \
  --game "unison=/path/to/build|/path/to/jp" \
  --game "original=/path/to/original"
```

### 直接读 `.mcdat` 成品

不解包时要额外提供一个解密模块：

```bash
export LV_LIB=/path/to/folder/with/lvlib
```

该目录下的 `lvlib.py` 只需提供 `xor_crypt(bytes) -> bytes`。行结构由本仓库的 `lvpage.py` 处理。

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `LV_GAME` | 游戏根目录，需含 `Config/` 与 `Data/_` | 必填 |
| `LV_JP` | 原版解包出的纯文本目录 | 解密 `LV_GAME` 自身 |
| `LV_LIB` | 含 `lvlib.py` 的目录 | 自动查找 |
| `LV_KEYWORDS` | 覆盖 `data/engine_keywords.json` | 仓库自带 |
| `LV_REFPATHS` | 覆盖 `data/refpaths.json`（路径普查，可加大 R1 覆盖） | 仓库自带 |
| `LV_INVENTORY` | 覆盖 `data/inventory.json`（脚本名到 id 的映射） | 仓库自带 |
| `LV_INTENTIONAL` | 覆盖白名单 `intentional.json` | 仓库自带 |
| `LV_REPORT_DIR` | `dev/make_acceptance.py` 的输出目录 | `out/` |

## 输出

```
report.md      按规则分组的报告
report.json    内容同上，仅格式不同
mapping.json   脚本名到内部 id 的映射（R2 使用）
```

行号 1-based，每条结果带 `line_base: 1`。

```bash
python linter.py --out-dir /path/to/dir    # 改输出位置
python linter.py --quiet                   # 只打印最后一行汇总
```

## origin

每条结果标一个来源，决定要不要处理：

| origin | 含义 |
|---|---|
| `translation` | 原版与成品该行不同，可能是本地化引入 |
| `inherited` | 两侧逐字相同，原版固有 |
| `intentional` | 命中白名单 |

只看 `translation` 即可，其余两类可以忽略。

## 白名单

主动做的改动不想被算进 `translation`，写进 `intentional.json`：

```json
{
  "entries": [
    { "file": "66442", "line": 451, "reason": "有意注释掉这个图层" }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | 脚本 id，不带扩展名 |
| `line` | 是 | 行号，1-based |
| `code` | 否 | 只匹配某类结果 |
| `reason` | 否 | 备注，出现在报告里 |
| `match` | 否 | 该行需包含此子串，防止行号漂移后误命中 |

文件缺失或 `entries` 为空都不影响运行。各游戏的白名单不同。

## 关于 Config/Commands.xml

编辑器用的命令参考手册。

本工具使用该文件来：

- 判断某个词是否命令
- 用 `syntax` 字段核对必填参数个数。

该文件在不同引擎上格式各不相同，目前已适配开头提到的三个版本。若遇到其他格式，
解析逻辑集中在 `lvparse.command_table()`，XML 样例与解析要点见
[`docs/COMMANDS_XML.md`](docs/COMMANDS_XML.md)。

## 已知限制

- 资源是数字命名的 `.mcdat`，原文件名打包时被剥离，路径到 id 不可反推。R1 只能比对引用集合，无法验证文件真实存在。
- 命令名有扩展形式（`移動x2`、`文字窓下段`），靠前缀匹配与脚本内的 `コマンドマクロ` 识别。
- 现有规则覆盖常规文本行。条件后直接跟台词（`~もし (cond) "文本`）、按钮回调里的嵌套命令需要各自单独处理。

## 目录

```
linter.py          入口
lvpage.py          行结构
lvparse.py         解析：行、命令表、命令识别
lvctx.py           语料缓存
lvfind.py          结果结构
lvroot.py          路径与环境变量
rule_r1..r6.py     六条规则
intentional.json   白名单
run_all3.py        多游戏批量驱动
data/              引擎元数据
docs/              格式说明
tests/             合成用例测试
dev/               变异基准、报告生成、确定性检查
```

## 开发

```bash
cd tests && python test_acceptance.py      # 合成用例，6 个已知缺陷形态
python dev/bench_mutation.py               # 变异基准，测各规则召回与误报
bash dev/_det.sh <game_dir> [jp_dir]       # 跑两次，比对 report.json 的 sha256
```

测试与基准需要设置 `LV_GAME` / `LV_JP` / `LV_LIB`。
