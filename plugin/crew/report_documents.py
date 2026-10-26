"""Keep an authored offline document separate from Crew's acceptance evidence."""
import html


MAX_HTML = 512 * 1024
CONTENT_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                  "img-src data: blob:; font-src data:; media-src data:; "
                  "base-uri 'none'; form-action 'none'")
VIEWER_POLICY = CONTENT_POLICY + "; frame-src about:"


def render(title, document, evidence):
    # srcdoc is escaped as an attribute, never interpolated into the viewer DOM.
    # The child has scripts for charts, but no origin, network, forms or top navigation.
    content = ('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' +
               html.escape(CONTENT_POLICY, quote=True) + '"><meta name="referrer" content="no-referrer">' + document)
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="%s">
<meta name="referrer" content="no-referrer"><title>%s</title>
<style>
html,body{margin:0;height:100%%}body{display:flex;flex-direction:column;background:#fafafa;color:#292929;font:16px/1.7 system-ui,sans-serif}
.crew-document{order:1;width:100%%;flex:1;min-height:0;border:0;background:white}
.crew-evidence{order:2;border-top:1px solid #ddd;flex:none}
.crew-evidence>summary{padding:12px 24px;cursor:pointer;background:#f3f5f3}
.crew-evidence[open]{flex:1;min-height:0;overflow:auto}.crew-evidence[open]+iframe{display:none}
.crew-evidence[open]>summary{position:sticky;top:0}.crew-evidence-body{max-width:960px;margin:auto;padding:0 24px 32px}
.crew-evidence-body svg{width:100%%;max-width:600px}.crew-evidence-body table{border-collapse:collapse;width:100%%}
.crew-evidence-body th,.crew-evidence-body td{border:1px solid #ddd;text-align:left;vertical-align:top;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere}
.crew-evidence-body p,.crew-evidence-body li{white-space:pre-wrap;overflow-wrap:anywhere}
@media print{html,body{height:auto}.crew-document{height:90vh}.crew-evidence{display:block}}
</style></head><body>
<details class="crew-evidence"><summary>验收证据与交付记录</summary><div class="crew-evidence-body">%s</div></details>
<iframe class="crew-document" title="%s · 报告正文" sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc="%s"></iframe>
</body></html>''' % (html.escape(VIEWER_POLICY, quote=True), html.escape(title), evidence,
                     html.escape(title, quote=True), html.escape(content, quote=True))
