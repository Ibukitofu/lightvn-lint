# Commands.xml 是什么

`Config/Commands.xml` 是**编辑器**用的命令参考手册，游戏运行时不读它——引擎的命令表编译在 exe 里。

它和 `Syntax_core.xshd`（语法高亮）、`Strings_editor.xml`（编辑器界面文字）放在同一个 `Config/` 目录，
由 `settings.xml` 的 `CommandFile` 属性指过去：

```xml
<Settings ... CommandFile="Commands.xml" SyntaxFile="Syntax_core.xshd" />
```

对本地化工具而言，它是唯一一份「命令名到必填参数个数」的权威来源。

## 三种格式

引擎 16.2 / 16.4 / 16.9 导出的结构不同，目前已适配这三种。若遇到其他格式，扩展 `lvparse.command_table()` 即可。

### A 型（16.9）

命令名在 `<Command>` 上：

```xml
<Command cmd="bg" name="背景">
  <explanation lang="jap" name="背景" syntax="背景 [ファイル名] (画面領域範囲)" />
</Command>
```

### B 型（16.4）

`<Command>` 只有 `cmd`，命令名在子节点：

```xml
<Command cmd="ninecg">
  <explanation lang="jap" name="九絵" syntax="九絵 [名前] [ファイル名] ..." />
</Command>
```

### C 型（16.2）

`<Command name>` 是分类标题，命令名同样在子节点：

```xml
<Command name="--[コマンド使用情報]--">
  <explanation lang="jap" name="--[コマンド使用情報]--" syntax="" />
</Command>
```

## 解析要点

优先读 `<explanation lang="jap">`，缺失时回退 `<Command name>` 并跳过 `--[...]--` 分类行：

```python
m = re.search(r'<explanation\s+lang="jap"\s+name="([^"]*)"', block)
if m:
    name = html.unescape(m.group(1))
else:
    m = re.search(r'<Command\b[^>]*?\bname="([^"]*)"', block)
    if m and not m.group(1).startswith('--['):
        name = html.unescape(m.group(1))
```

- 必须 `html.unescape`，值是 XML 转义过的（`&#xA;` 表示换行）
- `lang` 有 `jap` / `eng` 两种，取 `jap`

| 游戏 | `<Command>` | `<explanation>` | 解析出命令 |
|---|---|---|---|
| ユニゾンコード | 419 | 1,664 | 419 |
| 恋愛移植 | 405 | 1,215 | 405 |
| コンビニ珍道中 | 404 | 808 | 404 |

三者交集 400 条，差异来自游戏用到的功能和编辑器版本。

## 命令的四种识别来源

判断一个词是不是命令，不能只查 `Commands.xml`。按顺序查：

| 来源 | 内容 |
|---|---|
| `Commands.xml` | 官方命令表 |
| `Syntax_core.xshd` | 编辑器分词表，322 个关键字，日文名与英文 id 成对 |
| 脚本宏 | 脚本里 `~コマンドマクロ <名字> "..."` 定义的自定义命令 |
| 前缀匹配 | 已知命令 + 短后缀，如 `移動x2`、`文字窓下段` |

`Syntax_core.xshd` 在 16.2.1 / 16.4.1 / 16.9.2 三个版本里逐字节相同（`sha256 022f3810cb406086`），
提取一次即可通用。

## 前缀匹配的条件

只要求「以已知命令开头」不够：日文动词大量以短命令开头（`イン`、`アウト`），整句台词会被判成命令。

三个条件需同时满足：

1. 不含句子标点与引号：`，。、！？…「」『』（）"`
2. 不含反斜杠转义：`\w` 等
3. 后缀长度 1 到 4 字符

## 文件清单

| 文件 | 内容 |
|---|---|
| `Config/Commands.xml` | 命令名、syntax、说明 |
| `Config/Syntax_core.xshd` | 编辑器分词规则（AvalonEdit XSHD） |
| `Config/Syntax_lua.xshd` | Lua 部分的分词规则 |
| `Config/Strings_editor.xml` | 编辑器界面文字 |
| `Config/settings.xml` | 运行设置，并引用上面几个文件 |
