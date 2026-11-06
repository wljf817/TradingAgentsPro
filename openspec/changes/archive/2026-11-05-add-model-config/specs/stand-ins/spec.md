# Spec Delta

## MODIFIED Requirements

### Requirement: 替身的模型

替身用的模型写在模型文件里（model-config spec）。替身的模型配置 SHALL 是下表，每个 agent 只有主模型、没有备用模型，单价都是 0：

| agent | 模型（厂商/模型名） |
| --- | --- |
| PM | standin/pm |
| News | standin/news |
| Quant | standin/quant |
| Fundamentals | standin/fundamentals |
| Critic | standin-critic/critic |
| Reviewer | standin/reviewer |

- Critic 和 PM 的厂商不同，满足 model-fallback spec「厂商校验」
- 替身给出的输出，实际使用的模型就是表里这个 agent 的模型，不是备用模型；Result 和案件记录照常记下它

#### Scenario: 替身的模型配置

- **WHEN** 用替身的模型配置
- **THEN** 配置正确；PM、News、Quant、Fundamentals、Critic、Reviewer 的模型依次是上表的六个，都没有备用模型，单价都是 0

#### Scenario: 实际使用的模型

- **WHEN** 经 Agent 网关运行 BBB 的 Quant 的任务
- **THEN** Result 的模型是 `standin/quant`，不是备用模型
