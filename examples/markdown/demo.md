# Markdown viewer demo

Open this file with **F3** (View) in the file manager — `dunders` renders it
instead of showing the raw source.

## Inline images become ASCII art

The image below is a standalone `![alt](path)` line, so the viewer decodes it
and draws it as **inline ASCII art** using the file-manager's image converter:

![A tiny landscape with a sun and hills](landscape.png)

Remote images (`https://…`) and inline-in-a-paragraph images stay as the plain
🖼 placeholder — only standalone local images are converted.

## Formatting still works

- Lists, **bold**, *italic*, `inline code`
- Headings feed the **Contents** outline (toggle with `c`)

```python
def hello() -> str:
    return "fenced code blocks render too"
```

> Block quotes, tables, and the rest render through Textual's Markdown widget.

| Key | Action            |
|-----|-------------------|
| `t` | Raw ⇄ Rendered    |
| `c` | Toggle Contents   |

Press `t` to flip to the raw Markdown source and back.
