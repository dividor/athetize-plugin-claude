# Examples

## Bananas: a sample paper to try Athetize on

`bananas/` holds a short, made-up review paper about bananas. It exists so that you can see
what an Athetize check looks like without using a paper of your own. Some of its claims are
deliberately wrong.

| File | What it is |
| --- | --- |
| `banana_sample.md` | The paper's text: title, sections, figure captions and nine references |
| `fig1_trade.png`, `fig2_provitamin.png` | Its two figures, drawn from numbers in the cited sources |
| `make_docx.py` | Builds `banana_sample.docx`, laid out like a paper with a SAMPLE watermark |

Build the Word file, then ask Claude to check it:

```bash
python3 examples/bananas/make_docx.py
```

```text
Check the citations in examples/bananas/banana_sample.docx
```

The Word file is built, not stored, because this repository keeps no binary files other than
pictures. The builder needs nothing beyond Python itself.

### What the sample shows

Seven of the nine references are open-access papers that Athetize downloads by itself. One is a
report published on the web by the Food and Agriculture Organization, with its address printed
in the reference. One is a 1955 paper behind a paywall, for which only the abstract is available.

The claims were written to produce every kind of result:

- claims the cited source supports, such as the share of bananas that are traded
- claims that go further than the source, such as how long the wilt fungus survives in soil
- a claim the source contradicts, about fruit ripened with ethylene
- a claim that cites the wrong reference
- a claim with no citation at all
- a claim that rests on an abstract only, so its result is provisional
- a reference whose year is wrong in the reference list, which the check corrects

The screenshots in the main [README](../README.md) were taken from a check of this paper.
