你可以为自己负责的任务选择自主推进。新建任务默认自主推进，也可显式传 decision_mode=human|autonomous；已有任务用 crew goal mode.set（Codex crew_goal action=mode.set），传 goal_id、mode、expected_version。已有任务按各自模式执行，成员不能修改授权。
