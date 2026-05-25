"""Hello World Tool - nanobot 二次开发入门示例"""

from nanobot.agent.tools import Tool, tool_parameters


@tool_parameters({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "要问候的名字"
        },
        "greeting_type": {
            "type": "string",
            "enum": ["formal", "casual", "fun"],
            "default": "casual",
            "description": "问候类型：正式/随意/有趣"
        }
    },
    "required": ["name"]
})
class HelloWorldTool(Tool):
    """一个简单的自定义工具，用于学习 nanobot 二次开发"""
    
    @property
    def name(self) -> str:
        return "hello_world_tool"
    
    @property
    def description(self) -> str:
        return "返回个性化的问候语，支持多种风格"
    
    async def execute(self, name: str, greeting_type: str = "casual") -> str:
        """
        执行问候逻辑
        
        Args:
            name: 要问候的名字
            greeting_type: 问候风格
            
        Returns:
            格式化后的问候语
        """
        greetings = {
            "formal": f"尊敬的{name}先生/女士，您好！欢迎使用 nanobot 助手系统。",
            "casual": f"嗨，{name}！很高兴见到你 🎉",
            "fun": f"🚀 哇哦！{name}大佬驾到！nanobot 团队全体起立鼓掌 👏"
        }
        
        greeting = greetings.get(greeting_type, greetings["casual"])
        return greeting
