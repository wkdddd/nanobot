---
name: hello_world_tool
description: 一个最简单的自定义工具示例
metadata: {"nanobot":{"emoji":"👋"}}
---

# Hello World Tool

这是一个用于学习 nanobot 二次开发的入门级工具示例。

## 功能说明

这个工具接收一个名字参数，然后返回个性化的问候语。

## 使用方法

在对话中直接调用：

```
你好，帮我用 hello_world_tool 打个招呼，名字叫 Alice
```

或者通过 API 调用：

```python
from nanobot.agent.tools import Tool, tool_parameters

@tool_parameters({
    "type": "object",
    "properties": {
        "name": {"type": "string"},
    },
    "required": ["name"],
})
class HelloWorldTool(Tool):
    @property
    def name(self) -> str:
        return "hello_world_tool"
    
    @property
    def description(self) -> str:
        return "返回个性化的问候语"
    
    async def execute(self, name: str) -> str:
        return f"你好，{name}！欢迎来到 nanobot 的世界 🎉"
```

## 部署步骤

1. 复制此目录到 `~/.nanobot/skills/hello_world_tool/`
2. 同时创建 `tools/hello_world.py` 文件（见下方）
3. 重启 nanobot 即可使用

## 进阶开发

参考 `nanobot/agent/tools/base.py` 了解更复杂的工具实现：
- 添加多个参数
- 处理异步操作
- 使用外部 API
- 读写文件系统
- 执行 shell 命令
