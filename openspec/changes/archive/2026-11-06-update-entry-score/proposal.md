# Proposal

## Why

PM 现在可以一边决定开仓或加仓，一边给 0 分或负分，结论照样通过，因为代码只检查信号的格式，不看评分的正负。真实模型接入以前要补上这项检查，让决定和评分不能互相矛盾。

## What Changes

- 结论的校验加一项：决定是开仓方案或加仓时，PM 的信号评分必须大于 0，否则拒绝，原因是"开仓评分不大于 0"
- 这一项排在研究要求之前
- 不通过时和其他校验一样退回 PM 重交，共用一个案件的重交次数

引用完整版 Proposal 的章节：「Agent」的「PM」里的「校验」。

**Non-goals：**

- 升级为关注和寻股加入：不要求评分大于 0
- 买反向品种时评分必须小于 0：交易方案还没有品种，放到杠杆和反向品种那组
- 置信度下限：Proposal 没有这条规则

## Capabilities

### New Capabilities

无

### Modified Capabilities

- `case-types`：「结论的校验」加开仓评分这一项

## Impact

- 代码：`modules/scheduler/domain/validation.py` 的拒绝原因和校验，`workflows/case_entries.py` 的原因文字
- 测试：`tests/modules/scheduler/domain/test_validation.py`
- 替身：默认评分是 1，开仓和加仓照常通过
- 数据和部署：没有迁移，随下一次更新上服务器

## 需要人决定

无
