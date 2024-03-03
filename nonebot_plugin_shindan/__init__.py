import re
import traceback
from typing import List, Optional, Type, Union

from nonebot import get_driver, require
from nonebot.adapters import Bot, Event
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.rule import to_me
from nonebot.typing import T_Handler

require("nonebot_plugin_orm")
require("nonebot_plugin_alconna")
require("nonebot_plugin_userinfo")
require("nonebot_plugin_htmlrender")

from nonebot_plugin_alconna import (
    Alconna,
    AlconnaMatcher,
    Args,
    At,
    Image,
    UniMessage,
    on_alconna,
)
from nonebot_plugin_userinfo import get_user_info

from . import migrations
from .config import Config
from .manager import shindan_manager
from .model import ShindanConfig
from .shindanmaker import (
    download_image,
    get_shindan_title,
    make_shindan,
    render_shindan_list,
)

__plugin_meta__ = PluginMetadata(
    name="趣味占卜",
    description="使用ShindanMaker网站的趣味占卜",
    usage="发送“占卜列表”查看可用占卜\n发送“{占卜名} {名字}”使用占卜",
    type="application",
    homepage="https://github.com/noneplugin/nonebot-plugin-shindan",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_userinfo"
    ),
    extra={
        "example": "人设生成 小Q",
        "orm_version_location": migrations,
    },
)


matcher_sd = on_alconna(
    "占卜",
    aliases={"shindan", "shindanmaker"},
    rule=to_me(),
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_ls = on_alconna(
    "占卜列表",
    aliases={"可用占卜"},
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_add = on_alconna(
    Alconna("添加占卜", Args["id", int], Args["command", str]),
    permission=SUPERUSER,
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_del = on_alconna(
    Alconna("删除占卜", Args["id", int]),
    permission=SUPERUSER,
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_set_command = on_alconna(
    Alconna("设置占卜指令", Args["id", int], Args["command", str]),
    permission=SUPERUSER,
    use_cmd_start=True,
    block=True,
    priority=13,
)
matcher_set_mode = on_alconna(
    Alconna("设置占卜模式", Args["id", int], Args["mode", ["text", "image"]]),
    permission=SUPERUSER,
    use_cmd_start=True,
    block=True,
    priority=13,
)


@matcher_sd.handle()
async def _(matcher: Matcher):
    await matcher.finish(__plugin_meta__.usage)


@matcher_ls.handle()
async def _(matcher: Matcher):
    if not shindan_manager.shindan_list:
        await matcher.finish("尚未添加任何占卜")

    img = await render_shindan_list(shindan_manager.shindan_list)
    await UniMessage.image(raw=img).send()


@matcher_add.handle()
async def _(matcher: Matcher, id: int, command: str):
    for shindan in shindan_manager.shindan_list:
        if shindan.id == id:
            await matcher.finish("该占卜已存在")
        if shindan.command == command:
            await matcher.finish("该指令已被使用")

    title = await get_shindan_title(id)
    if not title:
        await matcher.finish("找不到该占卜，请检查id")

    await shindan_manager.add_shindan(id, command, title)
    refresh_matchers()
    await matcher.finish(f"成功添加占卜“{title}”，可通过“{command} 名字”使用")


@matcher_del.handle()
async def _(matcher: Matcher, id: int):
    if id not in (shindan.id for shindan in shindan_manager.shindan_list):
        await matcher.finish("尚未添加该占卜")

    await shindan_manager.remove_shindan(id)
    refresh_matchers()
    await matcher.finish("成功删除该占卜")


@matcher_set_command.handle()
async def _(matcher: Matcher, id: int, command: str):
    if id not in (shindan.id for shindan in shindan_manager.shindan_list):
        await matcher.finish("尚未添加该占卜")

    await shindan_manager.set_shindan(id, command=command)
    refresh_matchers()
    await matcher.finish("设置成功")


@matcher_set_mode.handle()
async def _(matcher: Matcher, id: int, mode: str):
    if id not in (shindan.id for shindan in shindan_manager.shindan_list):
        await matcher.finish("尚未添加该占卜")

    await shindan_manager.set_shindan(id, mode=mode)
    refresh_matchers()
    await matcher.finish("设置成功")


def shindan_handler(shindan: ShindanConfig) -> T_Handler:
    async def handler(
        bot: Bot,
        event: Event,
        matcher: Matcher,
        name: Optional[str] = None,
        at: Optional[At] = None,
    ):
        if at and (user_info := await get_user_info(bot, event, at.target)):
            name = user_info.user_displayname or user_info.user_name

        if not name and (
            user_info := await get_user_info(bot, event, event.get_user_id())
        ):
            name = user_info.user_displayname or user_info.user_name

        if name is None:
            await matcher.finish("无法获取名字，请加上名字再试")

        try:
            res = await make_shindan(shindan.id, name, shindan.mode)
        except Exception:
            logger.warning(traceback.format_exc())
            await matcher.finish("出错了，请稍后再试")

        msgs: List[Union[str, bytes]] = []
        if isinstance(res, str):
            img_pattern = r"((?:http|https)://\S+\.(?:jpg|jpeg|png|gif|bmp|webp))"
            for msg in re.split(img_pattern, res):
                if re.match(img_pattern, msg):
                    try:
                        img = await download_image(msg)
                        msgs.append(img)
                    except Exception:
                        logger.warning(f"{msg} 下载出错！")
                else:
                    msgs.append(msg)
        elif isinstance(res, bytes):
            msgs.append(res)

        if not msgs:
            await matcher.finish()

        uni_msg = UniMessage()
        for msg in msgs:
            if isinstance(msg, bytes):
                uni_msg += Image(raw=msg)
            else:
                uni_msg += msg
        await uni_msg.send()

    return handler


shindan_matchers: List[Type[AlconnaMatcher]] = []


def refresh_matchers():
    for matcher in shindan_matchers:
        matcher.destroy()
    shindan_matchers.clear()

    for shindan in shindan_manager.shindan_list:
        matcher = on_alconna(
            Alconna(shindan.command, Args["name?", str]["at?", At]),
            block=True,
            priority=14,
        )
        matcher.append_handler(shindan_handler(shindan))
        shindan_matchers.append(matcher)


driver = get_driver()


@driver.on_startup
async def _():
    await shindan_manager.load_shindan()
    refresh_matchers()
