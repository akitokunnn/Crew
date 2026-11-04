"""Isolated review fixture: real HTTP and SQLite with many leaders and tasks; every AI carrier is fake.

Run from the plugin directory: python -u tests/ui_review_fixture.py
It prints one JSON line with the URL, then keeps serving until stdin says "shutdown" or closes.
Nothing here touches the user's Crew home, database or running app.
"""
import datetime as dt
import json
import os
from pathlib import Path
import sys

for key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID'):
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tests  # noqa: F401  (test helpers, not shipped)
from test_goal_http import GoalHttpTests
from crew import board, goals, runtimes, todo

# 状态码：R 推进中、W 等你拍板、A 待验收、P 已暂停、Q 未开始、C 已完成。成员用组内序号表示。
GROUPS = [
    ('林序', 'codex', '产品交付与体验', [('知衡', 'codex', '前端实现'), ('远舟', 'claude', '接口与数据'), ('听澜', 'codex', '交互与回归验证'), ('时雨', 'claude', '界面与信息设计'), ('青禾', 'codex', '工程支持')], [
        ('R', '组织架构与任务归属', '012'), ('W', '对话与任务上下文', '03', '任务切换时的上下文提示，用方案 A 还是 B？'),
        ('P', '普通文件夹工作区迁移与历史接续', '2'), ('Q', '消息归属回归', ''), ('R', '侧栏导航信息层级重排', '12'),
        ('W', '顶部标题与常驻属性去重', '3', '顶部状态放标题右侧，还是收进详情？'), ('R', '工作台待处理排序规则', '1'),
        ('Q', '全局助手独立页面的返回路径', ''), ('R', '任务清单搜索与筛选', '23'), ('A', '交付与文档入口归位到任务内', '2'),
        ('Q', '音效设置分项文案', ''), ('R', '长中文名称截断与悬停全名', '3'), ('Q', '窄屏抽屉交互', '2'), ('P', '键盘快捷键一览', '1'),
        ('R', '深色模式对比度复核', '2'), ('Q', '草稿与阅读位置保留回归', '2'), ('C', '拍板卡片样式统一', '3'), ('Q', '空状态文案', ''),
        ('W', '通知文案精简', '3', '任务交付通知要不要带验收入口？'), ('Q', '设置页结构整理', ''), ('C', '图标线宽统一', '3'),
        ('Q', '加载骨架屏', ''), ('R', '错误提示给出可操作的下一步', '1'), ('C', '导航文案整理', '')]),
    ('见川', 'claude', '资料整理与检索', [('雾杉', 'claude', '材料归档'), ('序白', 'codex', '检索验证')], [
        ('R', '任务资料检索', '01'), ('R', '引用来源去重', '0'), ('Q', '资料摘要长度上限', ''), ('A', '检索结果排序口径', '0'),
        ('Q', '网页快照存档', '1'), ('R', 'PDF 表格抽取', '0'), ('Q', '资料过期提醒', ''), ('C', '检索日志脱敏', '0')]),
    ('闻笛', 'claude', '桌面端、安装与打包', [('若川', 'codex', '安装与更新'), ('栖梧', 'claude', '窗口与系统集成'), ('南星', 'codex', '打包流水线'), ('朝露', 'claude', '体验细节')], [
        ('W', 'Windows 安装包签名与 SmartScreen 信誉', '02', '签名证书用公司 EV 证书还是个人证书？'), ('R', '自动更新的断点续传', '0'),
        ('R', '托盘图标在高 DPI 下发虚', '1'), ('A', '多窗口位置与大小恢复', '13'), ('R', '退出时后台服务的优雅关闭', '1'),
        ('Q', '便携版数据目录迁移', ''), ('R', '安装路径含中文与空格', '02'), ('Q', '卸载残留清理', '0'), ('R', '崩溃日志收集与脱敏', '2'),
        ('W', '启动白屏的诊断提示', '3', '白屏超过 8 秒就提示重启服务，可以吗？'), ('Q', '深色模式跟随系统切换', ''), ('R', '窗口最小尺寸与短屏适配', '3'),
        ('Q', '快捷键冲突检测', ''), ('P', '通知中心与免打扰', '1'), ('Q', '字体回退与生僻字显示', ''), ('R', '文件拖放到对话', '13'),
        ('Q', '剪贴板图片粘贴', ''), ('C', '系统代理与证书', '0'), ('Q', '离线启动与重连提示', ''), ('R', '内置浏览器面板权限', '1'),
        ('Q', '多显示器 DPI 切换', ''), ('C', '应用菜单中文化', '3'), ('A', '首次启动引导精简', '3'), ('Q', '资源占用面板', ''),
        ('R', '安装包体积压缩', '2'), ('Q', '旧版本数据导入', ''), ('C', '日志轮转与上限', '2'), ('Q', 'ARM64 构建', ''),
        ('R', '打包流水线缓存', '2'), ('Q', '发布说明自动生成', '')]),
    ('青岚', 'codex', '后端服务与运行时', [('疏桐', 'codex', '存储与迁移'), ('白榆', 'claude', '执行与恢复'), ('临溪', 'codex', '接口与快照')], [
        ('R', '会话恢复与断线续跑', '1'), ('W', '执行代次与轮次一致性', '12', '旧轮次的执行要不要允许继续查看日志？'), ('R', '任务暂停的真实停止确认', '1'),
        ('Q', '上下文压缩的触发阈值', ''), ('A', '用量统计按执行归属', '2'), ('R', '多工作区索引重建', '0'), ('Q', '数据库迁移回滚演练', '0'),
        ('C', '拍板记录的来源追溯', '2'), ('R', '消息去重与幂等写入', '02'), ('Q', '后台服务端口冲突', ''), ('R', '白板快照增量推送', '2'),
        ('P', '长任务心跳与超时', '1'), ('Q', '原生审批弹窗回传', ''), ('C', '错误原因原样上抛', '1'), ('R', '并发派工的锁粒度', '0'),
        ('Q', '历史记录分页加载', ''), ('Q', '存储目录权限检查', ''), ('R', '定时任务与唤醒节流', '1'), ('Q', '附件存储去重', ''),
        ('R', '快照接口字段瘦身', '2'), ('Q', '运行时切换不丢上下文', ''), ('C', '日志结构化字段', '0')]),
    ('多载体适配与回归验证（CC / Codex 原生能力与长时间稳定性）', 'codex', '两种 AI 载体的原生能力适配、长时间运行与回归',
     [('鹤年', 'claude', '子代理与工具'), ('鸣珂', 'codex', '原生能力'), ('远黛', 'codex', '回归矩阵'), ('拾光', 'claude', '长时间稳定性')], [
        ('R', 'CC 子代理结果回传', '0'), ('W', 'Codex effort 档位映射', '1', '高档位默认给验收任务，还是只给施工任务？'), ('R', '模型清单原生发现', '1'),
        ('Q', '两种载体的权限提示统一', ''), ('R', '长时间运行 8 小时稳定性', '3'), ('A', '载体崩溃后的自动接续', '03'), ('R', '工具调用失败原因展示', '0'),
        ('Q', '载体切换时的草稿保留', ''), ('Q', '原生斜杠命令透传', ''), ('R', '回归用例双载体矩阵', '2'), ('Q', '计费与用量口径对齐', ''),
        ('P', '沙箱路径差异处理', '1'), ('Q', '中文路径兼容', ''), ('R', '超长输出截断策略', '02')]),
    ('守望', 'codex', '新建小组', [], []),
]
STATUS = {'R': 'running', 'W': 'waiting_user', 'A': 'awaiting_acceptance', 'P': 'paused', 'Q': 'queued', 'C': 'completed'}


def seed(case):
    conn, project = case.conn, case.project
    leaders = []
    for gi, (name, runtime, duty, members, tasks) in enumerate(GROUPS):
        sid = 'review-leader-%d' % gi
        board.join(conn, sid, 'demo', name, project, project, 'main', task=duty, role='leader')
        runtimes.set(conn, sid, runtime)
        member_ids = []
        for mi, (member, member_runtime, role) in enumerate(members):
            msid = 'review-member-%d-%d' % (gi, mi)
            board.join(conn, msid, 'demo', member, project, project, 'main', task=role, group_name=name)
            runtimes.set(conn, msid, member_runtime)
            member_ids.append((msid, member_runtime, role))
        for ti, spec in enumerate(tasks):
            letter, title, picks = spec[:3]
            ask = spec[3] if len(spec) > 3 else None
            goal = goals.create(conn, 'demo', sid, title, ask or ('%s：按任务要求推进，保留证据。' % title), ['交付可核对证据'])
            status = STATUS[letter]
            picked = [member_ids[int(digit)] for digit in picks]
            for msid, _, role in picked:
                goals.assign(conn, goal['id'], msid, role)
            if status != 'queued':
                lead = goals.create_execution(conn, goal['id'], sid, runtime=runtime)
                goals.set_execution_status(conn, lead['id'], 'running' if status == 'running' else 'idle')
                for order, (msid, member_runtime, _) in enumerate(picked):
                    execution = goals.create_execution(conn, goal['id'], msid, runtime=member_runtime)
                    member_status = {'running': 'idle' if order else 'running', 'waiting_user': 'idle', 'paused': 'paused',
                                     'awaiting_acceptance': 'completed', 'completed': 'completed'}[status]
                    goals.set_execution_status(conn, execution['id'], member_status)
                if ask:
                    board.ask(conn, sid, ask, ['A. 按建议执行', 'B. 换一种做法'], recommendation='A', execution_id=lead['id'])
            stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=(gi * 53 + ti * 37) % 600 + 2)).isoformat()
            conn.execute('UPDATE goals SET status=?, manual_pause=?, updated_at=? WHERE id=?',
                         (status, 1 if status == 'paused' else 0, stamp, goal['id']))
        leaders.append(sid)
    # 总会话里的拍板不属于任何任务。
    board.ask(conn, leaders[4], '长时间稳定性测试要不要占用夜间的两台机器？', ['A. 占用', 'B. 白天跑'], recommendation='A')
    todo.add(conn, 'review-member-0-0', '确认录音测试环境', '打开测试页面', '结果可核对')
    todo.add(conn, 'review-member-2-0', '准备签名证书', '从公司证书库导出', '签名后安装不再弹 SmartScreen')
    conn.commit()
    return leaders


def main():
    case = GoalHttpTests('test_starting_goal_keeps_general_native_available')
    case.setUp()
    try:
        case.server.service.now = lambda: dt.datetime.now(dt.timezone.utc)
        leaders = seed(case)
        print(json.dumps({'url': case.url, 'project': 'demo', 'home': str(case.home), 'leaders': leaders}, ensure_ascii=False), flush=True)
        stopped = False
        for line in sys.stdin:
            if line.strip() == 'shutdown':
                stopped = True
                break
        if not stopped:
            # 没接标准输入时（后台启动）一直服务，直到进程被结束。
            import threading
            threading.Event().wait()
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
