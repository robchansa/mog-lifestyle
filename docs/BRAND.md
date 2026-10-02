# MOG Lifestyle — brand identity

The intake form answered **"No — need branding too"** and **"Elegant / Luxury"**,
with a palette of **black & white** and blancc.com given as a reference. This is
the identity built from that brief.

---

## Positioning

**MOG Lifestyle** makes performance apparel and training essentials for people
who treat training as a standard rather than a hobby — the "community of high
achievers and health / fitness lovers" named in the brief.

**Line** Built for the work.
**Voice** Direct, unembellished, quietly confident. Short sentences. No hype, no
exclamation marks, no motivational shouting. State the fact and stop.

> Heavyweight jersey that holds its shape. Four-way stretch that moves in every
> direction you do. Every piece is tested through a full training block before it
> goes on sale.

---

## Logotype

The client supplied the logotype and monogram. Both are stored as **alpha
masks** in `app/static/brand/` and painted with `currentColor` in CSS:

```css
.wordmark__mark {
  background: currentColor;
  mask: url("/static/brand/wordmark.png") no-repeat center / contain;
}
```

One asset therefore serves light mode, dark mode, the admin sidebar and
anything laid over photography — there is no second "white version" to keep in
sync, and no chance of shipping a black logo on a black header.

| Asset | Source | Use |
| --- | --- | --- |
| `wordmark.png` | supplied logo sheet | Header, footer, admin, `Organization.logo` |
| `monogram.png` | supplied logo sheet | Avatars, care labels, compact lockups |
| `favicon.png` | monogram on ink | Browser tab, apple-touch-icon |
| `og.jpg` | campaign frame, 1200×630 | Social share card |

Regenerate all of them from new source artwork with:

```bash
python3 tools/build_assets.py /path/to/source/images
```

The pipeline finds each image by what it contains rather than its filename: a
light-background sheet with two content bands is the logo, a light-background
sheet with two garments is a flat-lay, and anything else is campaign
photography bucketed by aspect ratio.

**Clear space** equal to the height of the `O` on all four sides.
**Minimum size** 64px wide for the wordmark; below that use the monogram.

**Never** recolour it outside the monochrome palette, add a gradient, outline
it, stretch it off its 1087:304 ratio, or place it where contrast drops below
4.5:1. The mask approach makes the first and last of those hard to do by
accident.

## Colour

Strictly monochrome, as specified. There is no accent colour; the only chroma on
the site is whatever appears in product photography.

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--ink` | `#0b0b0c` | `#f4f2ef` | Body text, primary buttons, rules |
| `--ink-soft` | `#2a2a2e` | `#d8d5d0` | Secondary text |
| `--muted` | `#6c6c74` | `#9d9aa2` | Captions, labels, metadata |
| `--faint` | `#9a9aa1` | `#6f6d75` | Disabled, placeholder |
| `--hairline` | `#e2dfd9` | `#26262b` | Borders — never a drop shadow |
| `--paper` | `#faf9f7` | `#0b0b0c` | Page ground |
| `--surface` | `#ffffff` | `#121215` | Inputs, raised panels |
| `--raised` | `#f3f1ee` | `#17171b` | Image placeholders, table headers |

Every value is neutral by construction — `tests/test_brief.py` fails the build if
any hex in the stylesheet has more than 12 points of spread between its channels.
That is the brief enforced as a test rather than a note in a file.

The full palette inverts under `prefers-color-scheme: dark`; the store is legible
in either without a toggle.

---

## Typography

```
--font-display: "Didot", "Bodoni 72", "Hoefler Text", "Playfair Display",
                "Times New Roman", Times, serif;
--font-sans:    -apple-system, BlinkMacSystemFont, "Segoe UI",
                "Helvetica Neue", Inter, Arial, sans-serif;
```

Both stacks are system-resident: **no webfont request, no layout shift, no
third-party origin** — which also keeps the Content-Security-Policy tight.

| Role | Treatment |
| --- | --- |
| Display | Didone, 400 weight, `line-height: .98`, `letter-spacing: -.015em` |
| Eyebrow / label | Sans, uppercase, `letter-spacing: .2em`, `--step--1` |
| Body | Sans, `line-height: 1.65`, max `62ch` |
| Buttons | Sans, uppercase, `letter-spacing: .2em`, 500 weight |
| Numerals | `font-variant-numeric: tabular-nums` everywhere money appears |

The scale is fluid (`clamp()` from `--step--1` to `--step-5`), so nothing needs a
breakpoint to stay in proportion.

---

## Layout & surface

- **Hairlines, not shadows.** One pixel of `--hairline` does every job a box
  shadow would. There is not a single `box-shadow` on a surface in the system.
- **Square corners.** `border-radius: 0` throughout, including inputs and
  buttons. The only radius in the product is the cart-count pip.
- **Air.** Section rhythm is `clamp(3.5rem, 2vw + 2rem, 8rem)`.
- **4:5 imagery.** Every product frame is portrait, cropped with `object-fit`.
- **Motion is a whisper.** 320ms, `cubic-bezier(.22,.61,.36,1)`, and a 1.035×
  image scale on hover. All of it disabled under `prefers-reduced-motion`.

---

## Product imagery

Two kinds of imagery are in play.

**Photography (preferred).** The supplied flat-lays and campaign frames are cut
by `tools/build_assets.py` into:

| Asset | Frame |
| --- | --- |
| `img/mog-tee-front.jpg`, `-back.jpg` | 4:5, cropped to the garment |
| `img/lifestyle-tee-front.jpg`, `-back.jpg` | 4:5, cropped to the garment |
| `img/campaign-wide.jpg` | 16:9 hero |
| `img/campaign-portrait.jpg` | 4:5 editorial |
| `img/campaign-tall.jpg` | 9:16, mobile hero and about page |

A product carries these in its `images` column (newline-separated paths), set
from the admin console or the seed.

**Generated fallback.** Products with no photography fall back to `app/art.py`,
which renders a monochrome studio still from an `art_seed` like `hoodie-02` —
fifteen garment silhouettes (tee, long sleeve, hoodie, work shirt, crop top, tank, sports bra, joggers, shorts, cap, beanie, ski mask, socks, bottle, duffel) across six tonal treatments. `catalog.image_list()`
resolves whichever applies, so nothing downstream has to care.

To shoot more product to match: seamless light-grey sweep, single key from the
upper left, garment centred and laid flat, two-up front/back in one frame at
3:2 — that is the layout the pipeline already splits automatically.

## Applying it elsewhere

Packaging, care labels, email and social all inherit the same three rules:
monochrome, letter-spaced caps for anything small, didone for anything large.
The transactional email templates in `app/mailer.py` are the reference
implementation for off-site use.
