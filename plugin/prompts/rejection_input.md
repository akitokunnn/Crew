用户验收后要求修改：{{note}}
这条意见已写入任务需求，不用再 `goal_amend`。在本任务内按它修改：用 `goal_get` 看更新后的需求、`goal_work_list` 找出受影响的工作，复用已有成果，不从头重做。改完并验证后，重新写报告并用 `goal_finish` 提交，outcome=awaiting_acceptance；finish 成功后任务才会回到「待验收」。
