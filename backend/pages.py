from __future__ import annotations

import html
from typing import Any


def _safe(value: Any) -> str:
    return html.escape(str("" if value is None else value), quote=True)


def render_app_page(
    app: dict[str, Any],
    *,
    repo_name: str,
    repo_url: str,
    add_url: str,
    page_url: str,
) -> str:
    name = _safe(app.get("name") or app.get("id"))
    bundle_id = _safe(app.get("id"))
    summary = _safe(app.get("summary") or "Available as a signed Android package.")
    version = _safe(app.get("version") or "Latest")
    version_code = _safe(app.get("version_code"))
    download_url = _safe(app.get("download_url"))
    source_url = _safe(app.get("source_url"))
    icon_url = _safe(app.get("icon_url"))
    safe_repo_name = _safe(repo_name)
    safe_repo_url = _safe(repo_url)
    safe_add_url = _safe(add_url)
    safe_page_url = _safe(page_url)
    initials = _safe("".join(word[:1] for word in str(app.get("name") or app.get("id")).split())[:2].upper())
    icon = (
        f'<img src="{icon_url}" alt="" loading="eager">'
        if icon_url
        else f'<span aria-hidden="true">{initials}</span>'
    )
    source_link = (
        f'<a class="quiet-link" href="{source_url}" target="_blank" rel="noreferrer">View source ↗</a>'
        if source_url
        else ""
    )
    code_row = (
        f'<div><dt>Version code</dt><dd>{version_code}</dd></div>'
        if version_code
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{summary}">
  <meta name="theme-color" content="#07110f">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{name} — {safe_repo_name}">
  <meta property="og:description" content="{summary}">
  <meta property="og:url" content="{safe_page_url}">
  {f'<meta property="og:image" content="{icon_url}">' if icon_url else ''}
  <title>{name} — {safe_repo_name}</title>
  <style>
    @font-face {{
      font-family: "Inter";
      src: url("/assets/fonts/InterVariable.woff2") format("woff2");
      font-style: normal;
      font-weight: 100 900;
      font-display: swap;
    }}
    :root {{
      color-scheme: dark;
      --bg: #07110f;
      --surface: #10211d;
      --surface-soft: rgba(18, 34, 30, .76);
      --line: rgba(177, 232, 210, .14);
      --text: #f5fbf8;
      --muted: #9db2aa;
      --accent: #7ef0bd;
      --accent-hover: #95f5ca;
      --ink: #062217;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 78% 2%, rgba(73,216,157,.18), transparent 34rem),
        radial-gradient(circle at 5% 75%, rgba(41,108,90,.13), transparent 30rem),
        var(--bg);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-optical-sizing: auto;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      line-height: 1.5;
    }}
    a {{ color: inherit; }}
    .shell {{ width: min(1000px, calc(100% - 40px)); margin: 0 auto; }}
    .nav {{ display: flex; align-items: center; justify-content: space-between; min-height: 76px; border-bottom: 1px solid var(--line); }}
    .brand {{ display: inline-flex; align-items: center; gap: 11px; font-weight: 760; text-decoration: none; letter-spacing: -.02em; }}
    .brand-mark {{ display: grid; place-items: center; width: 36px; height: 36px; border-radius: 11px; color: var(--ink); background: var(--accent); }}
    .nav-links {{ display: flex; gap: 8px; }}
    .nav-link {{ padding: 10px 13px; border-radius: 11px; color: var(--muted); text-decoration: none; font-size: .9rem; }}
    .nav-link:hover {{ color: var(--text); background: rgba(255,255,255,.05); }}
    main {{ padding: 72px 0 92px; }}
    .app-hero {{ display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(290px, .65fr); gap: 52px; align-items: start; }}
    .identity {{ display: flex; gap: 24px; align-items: center; }}
    .icon {{ width: 112px; height: 112px; flex: none; display: grid; place-items: center; overflow: hidden; border: 1px solid rgba(126,240,189,.22); border-radius: 29px; color: var(--accent); background: linear-gradient(145deg, #193c32, #10271f); box-shadow: 0 22px 55px rgba(0,0,0,.25); font-size: 2rem; font-weight: 820; }}
    .icon img {{ width: 100%; height: 100%; object-fit: cover; }}
    .kicker {{ margin-bottom: 8px; color: var(--accent); font-size: .76rem; font-weight: 760; letter-spacing: .11em; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: clamp(2.6rem, 7vw, 5rem); line-height: 1; letter-spacing: -.06em; }}
    .bundle {{ display: inline-flex; align-items: center; gap: 9px; max-width: 100%; margin-top: 12px; color: var(--muted); font: .78rem ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .bundle span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .copy {{ padding: 0; border: 0; color: var(--accent); background: transparent; font: inherit; font-family: Inter, ui-sans-serif, system-ui, sans-serif; font-weight: 740; cursor: pointer; }}
    .summary {{ max-width: 700px; margin: 34px 0 30px; color: var(--muted); font-size: 1.12rem; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .button {{ display: inline-flex; align-items: center; justify-content: center; gap: 10px; min-height: 52px; padding: 0 20px; border: 1px solid var(--line); border-radius: 14px; color: var(--text); background: rgba(255,255,255,.035); font-weight: 760; text-decoration: none; transition: transform .18s ease, border-color .18s ease, background .18s ease; }}
    .button:hover {{ transform: translateY(-2px); border-color: rgba(126,240,189,.34); background: rgba(255,255,255,.07); }}
    .button-primary {{ color: var(--ink); background: var(--accent); border-color: var(--accent); box-shadow: 0 16px 42px rgba(73,216,157,.14); }}
    .button-primary:hover {{ background: var(--accent-hover); border-color: var(--accent-hover); }}
    .release-card {{ padding: 25px; border: 1px solid var(--line); border-radius: 24px; background: linear-gradient(145deg, rgba(24,48,41,.94), rgba(11,25,21,.9)); box-shadow: 0 30px 70px rgba(0,0,0,.2); }}
    .release-label {{ color: var(--muted); font-size: .75rem; font-weight: 740; letter-spacing: .1em; text-transform: uppercase; }}
    .release-title {{ margin: 10px 0 20px; font-size: 1.45rem; letter-spacing: -.035em; }}
    dl {{ margin: 0; }}
    dl > div {{ display: flex; justify-content: space-between; gap: 18px; padding: 13px 0; border-top: 1px solid var(--line); }}
    dt {{ color: var(--muted); font-size: .84rem; }}
    dd {{ margin: 0; font-size: .84rem; font-weight: 700; text-align: right; }}
    .quiet-link {{ display: inline-block; margin-top: 20px; color: var(--accent); font-size: .86rem; font-weight: 720; text-decoration: none; }}
    .explain {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 64px; }}
    .panel {{ padding: 25px; border: 1px solid var(--line); border-radius: 21px; background: var(--surface-soft); }}
    .panel h2 {{ margin: 0 0 9px; font-size: 1.08rem; letter-spacing: -.02em; }}
    .panel p {{ margin: 0; color: var(--muted); font-size: .9rem; }}
    .share {{ margin-top: 16px; padding: 18px; border: 1px solid var(--line); border-radius: 17px; background: rgba(3,15,12,.48); }}
    .share-label {{ display: block; margin-bottom: 9px; color: var(--muted); font-size: .75rem; font-weight: 730; text-transform: uppercase; letter-spacing: .09em; }}
    .share-row {{ display: flex; align-items: center; gap: 12px; }}
    .share-row code {{ min-width: 0; flex: 1; overflow: hidden; color: #cbe1d9; font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }}
    footer {{ display: flex; justify-content: space-between; gap: 18px; padding: 28px 0 42px; border-top: 1px solid var(--line); color: var(--muted); font-size: .78rem; }}
    @media (max-width: 760px) {{
      .app-hero {{ grid-template-columns: 1fr; }}
      .release-card {{ max-width: 560px; }}
    }}
    @media (max-width: 560px) {{
      .shell {{ width: min(100% - 26px, 1000px); }}
      .nav {{ min-height: 66px; }}
      .nav-link:first-child {{ display: none; }}
      main {{ padding: 50px 0 70px; }}
      .identity {{ align-items: flex-start; gap: 17px; }}
      .icon {{ width: 80px; height: 80px; border-radius: 22px; font-size: 1.45rem; }}
      h1 {{ font-size: clamp(2.25rem, 12vw, 3.4rem); }}
      .summary {{ margin-top: 26px; }}
      .button {{ width: 100%; }}
      .explain {{ grid-template-columns: 1fr; }}
      footer {{ flex-direction: column; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
  </style>
</head>
<body>
  <header class="shell nav">
    <a class="brand" href="/">
      <span class="brand-mark" aria-hidden="true">↘</span>
      <span>{safe_repo_name}</span>
    </a>
    <nav class="nav-links" aria-label="App navigation">
      <a class="nav-link" href="{safe_repo_url}">Repository ↗</a>
      <a class="nav-link" href="/">All apps</a>
    </nav>
  </header>

  <main class="shell">
    <section class="app-hero">
      <div>
        <div class="identity">
          <div class="icon">{icon}</div>
          <div>
            <div class="kicker">Independent Android app</div>
            <h1>{name}</h1>
            <div class="bundle"><span id="bundle-id">{bundle_id}</span><button class="copy" data-copy="bundle-id" type="button">Copy</button></div>
          </div>
        </div>
        <p class="summary">{summary}</p>
        <div class="actions">
          <a class="button button-primary" href="{safe_add_url}">Add repository to F-Droid ↗</a>
          <a class="button" href="{download_url}" download>Download latest APK ↓</a>
        </div>
      </div>

      <aside class="release-card" aria-label="Latest release">
        <div class="release-label">Current release</div>
        <h2 class="release-title">Latest stable version</h2>
        <dl>
          <div><dt>Version</dt><dd>{version}</dd></div>
          {code_row}
          <div><dt>Package</dt><dd>APK</dd></div>
          <div><dt>Updates</dt><dd>F-Droid repository</dd></div>
        </dl>
        {source_link}
      </aside>
    </section>

    <section class="explain" aria-label="Download options">
      <article class="panel">
        <h2>Updates without a gatekeeper</h2>
        <p>Add the repository to F-Droid for signed releases and future updates directly from the publisher.</p>
      </article>
      <article class="panel">
        <h2>Direct means direct</h2>
        <p>The APK button points to the newest version currently published in this repository—no account or store redirect.</p>
      </article>
    </section>

    <section class="share" aria-label="Share this app">
      <span class="share-label">Share this app page</span>
      <div class="share-row">
        <code id="share-url">{safe_page_url}</code>
        <button class="copy" data-copy="share-url" type="button">Copy link</button>
      </div>
    </section>
  </main>

  <footer class="shell">
    <span>Latest release only. Version archives can be added later.</span>
    <a href="/">Browse all apps →</a>
  </footer>

  <script>
    document.querySelectorAll('[data-copy]').forEach(button => {{
      button.addEventListener('click', async () => {{
        const target = document.getElementById(button.dataset.copy);
        try {{
          await navigator.clipboard.writeText(target.textContent);
          const label = button.textContent;
          button.textContent = 'Copied';
          window.setTimeout(() => {{ button.textContent = label; }}, 1500);
        }} catch (_) {{
          button.textContent = 'Select text';
        }}
      }});
    }});
  </script>
</body>
</html>"""


def render_app_not_found(bundle_id: str, repo_name: str) -> str:
    safe_bundle = _safe(bundle_id)
    safe_repo_name = _safe(repo_name)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#07110f"><title>App not found — {safe_repo_name}</title>
<style>
  @font-face {{ font-family: "Inter"; src: url("/assets/fonts/InterVariable.woff2") format("woff2"); font-style: normal; font-weight: 100 900; font-display: swap; }}
  :root {{ color-scheme: dark; }} * {{ box-sizing: border-box; }}
  body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 24px; color: #f5fbf8; background: radial-gradient(circle at 50% 0, #13342b, #07110f 48%); font-family: Inter, system-ui, sans-serif; font-optical-sizing: auto; -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }}
  main {{ width: min(560px, 100%); padding: 38px; border: 1px solid rgba(177,232,210,.16); border-radius: 24px; background: rgba(18,34,30,.8); text-align: center; }}
  .mark {{ width: 58px; height: 58px; margin: 0 auto 20px; display: grid; place-items: center; border-radius: 17px; color: #062217; background: #7ef0bd; font-size: 1.4rem; font-weight: 800; }}
  h1 {{ margin: 0 0 10px; font-size: clamp(2rem, 7vw, 3rem); letter-spacing: -.05em; }}
  p {{ margin: 0 0 25px; color: #9db2aa; }} code {{ color: #cce7dd; }}
  a {{ display: inline-flex; min-height: 48px; align-items: center; padding: 0 18px; border-radius: 13px; color: #062217; background: #7ef0bd; font-weight: 750; text-decoration: none; }}
</style></head><body><main><div class="mark">?</div><h1>App not found</h1><p>No current release exists for <code>{safe_bundle}</code>.</p><a href="/">Browse available apps</a></main></body></html>"""
