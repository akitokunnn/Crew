"""Keep an authored offline document separate from Crew's acceptance evidence."""
import html


MAX_HTML = 512 * 1024
CONTENT_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                  "img-src data: blob:; font-src data:; media-src data:; "
                  "base-uri 'none'; form-action 'none'")
VIEWER_POLICY = CONTENT_POLICY + "; frame-src about:"


def verification(data):
    """A compact evidence appendix; the saved report data remains the full audit."""
    esc = lambda value: html.escape(str(value), quote=True)
    labels = {'passed': '通过', 'failed': '失败', 'unverified': '未验证', 'simulated': '模拟验证'}
    parts = ['<p class="verification-note">以下为 AI 登记的验证结果，未验证与模拟验证不等于通过。</p>']
    for item in data['acceptance']:
        sources = item.get('evidence', [])
        body = '<ul>' + ''.join('<li>%s：%s</li>' % (esc(source['label']), esc(source['reference'])) for source in sources) + '</ul>' if sources else '<p>尚未登记证据。</p>'
        parts.append('<details class="verification-item"><summary><span>%s</span><strong data-status="%s">%s</strong></summary>%s</details>' %
                     (esc(item['criterion']), esc(item['status']), labels[item['status']], body))
    if data['examples']:
        parts.append('<details class="verification-examples"><summary>验证样例 · %s</summary>' % len(data['examples']))
        for example in data['examples']:
            parts.append('<h3>%s</h3><table>%s</table>' % ('实测' if example['kind'] == 'actual' else '示意（非实测证据）',
                ''.join('<tr><th>%s</th><td>%s</td></tr>' % (label, esc(example[key])) for key, label in
                        (('input', '输入 / 操作'), ('expected', '期望'), ('actual', '实际'), ('conclusion', '结论')))))
        parts.append('</details>')
    if data['unresolved']:
        parts.append('<h3>尚未解决</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % esc(item) for item in data['unresolved']))
    return ''.join(parts)


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
.crew-evidence>summary{padding:9px 20px;cursor:pointer;background:#f3f5f3;font-size:14px}
.crew-evidence[open]{flex:1;min-height:0;overflow:auto}.crew-evidence[open]+iframe{display:none}
.crew-evidence[open]>summary{position:sticky;top:0}.crew-evidence-body{max-width:960px;margin:auto;padding:0 24px 32px}
.crew-evidence-body svg{width:100%%;max-width:600px}.crew-evidence-body table{border-collapse:collapse;width:100%%}
.crew-evidence-body th,.crew-evidence-body td{border:1px solid #ddd;text-align:left;vertical-align:top;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere}
.crew-evidence-body p,.crew-evidence-body li{white-space:pre-wrap;overflow-wrap:anywhere}
.verification-note{font-size:13px;color:#666}.verification-item{border-bottom:1px solid #ddd;padding:10px 0}.verification-item>summary{display:flex;gap:16px;justify-content:space-between;cursor:pointer}.verification-item strong{font-size:13px;white-space:nowrap;font-weight:500}.verification-item strong[data-status=passed]{color:#366448}.verification-item strong[data-status=failed]{color:#a33a32}.verification-item strong:is([data-status=unverified],[data-status=simulated]){color:#856019}.verification-item ul{font-size:14px}.verification-examples{margin-top:16px}.verification-examples>summary{cursor:pointer}
@media print{html,body{height:auto}.crew-document{height:90vh}.crew-evidence{display:block}}
</style></head><body>
<details class="crew-evidence"><summary>验证详情</summary><div class="crew-evidence-body">%s</div></details>
<iframe class="crew-document" title="%s · 报告正文" sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc="%s"></iframe>
</body></html>''' % (html.escape(VIEWER_POLICY, quote=True), html.escape(title), evidence,
                     html.escape(title, quote=True), html.escape(content, quote=True))
