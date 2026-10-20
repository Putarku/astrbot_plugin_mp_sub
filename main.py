"""
MoviePilot 订阅插件

基于 AstrBot 框架的 MoviePilot 订阅管理插件，
支持搜索订阅影片和查看下载进度。
"""

from typing import Any
import asyncio
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageEventResult
from .api import MoviepilotApi


@register(
    "moviepilot_sub",
    "4Nest",
    "MoviePilot 订阅管理插件",
    "2.0.0",
    "https://github.com/4Nest/astrbot_plugin_mp_sub",
)
class MoviePilotPlugin(Star):
    """MoviePilot 订阅插件主类"""

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.config = config
        self.api = MoviepilotApi(config)
        self.state: dict[str, dict[str, Any]] = {}
        self.state_lock = asyncio.Lock()

        # 验证配置
        self._validate_config()

    def _validate_config(self) -> None:
        """验证配置并在启动时报告问题"""
        valid, error_msg = self.api.validate_config()
        if not valid:
            logger.error(f"[MoviePilot] 配置错误: {error_msg}")
        else:
            logger.info("[MoviePilot] 配置验证通过")

    @staticmethod
    def _md(event: AstrMessageEvent, text: str) -> MessageEventResult:
        """构建回复消息，按平台决定是否使用 Markdown

        QQ 官方机器人（qq_official）原生渲染 Markdown；其他平台（如 aiocqhttp）
        自动剥离 Markdown 语法后以纯文本发送。
        """
        plain = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", text)
        plain = plain.replace("**", "").replace("`", "")
        plain = re.sub(r"^#+\s*", "", plain, flags=re.MULTILINE)
        plain = re.sub(r"^>\s*", "", plain, flags=re.MULTILINE)
        return event.plain_result(plain).use_markdown(False)

    @staticmethod
    def _media_link(media: dict[str, Any]) -> str:
        """获取影片的 TMDB 详情页链接，用于区分同名影片"""
        link = media.get("detail_link")
        if link:
            return link
        tmdb_id = media.get("tmdb_id")
        if tmdb_id and str(tmdb_id) not in ("tv", "movie"):
            kind = "tv" if media.get("type") == "电视剧" else "movie"
            return f"https://www.themoviedb.org/{kind}/{tmdb_id}"
        return ""

    @filter.command("mp_help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """📺 MoviePilot 订阅助手

可用命令
• /sub 片名    搜索并订阅电影或电视剧
• /download    查看当前下载任务
• /mp_help     查看本帮助

使用说明
• 搜索后回复序号选择影片
• 电视剧随后回复季数
• 回复 0 可取消，选择等待 60 秒"""
        yield self._md(event, help_text)

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    @filter.command("sub")
    async def subscribe(self, event: AstrMessageEvent, message: str):
        """
        订阅影片

        Args:
            message: 影片名称
        """
        # 检查输入
        if not message or not message.strip():
            yield self._md(event, "❌ 请输入影片名称，例如：`/sub 星际穿越`")
            return

        # 检查 API 配置
        valid, error_msg = self.api.validate_config()
        if not valid:
            yield self._md(event, f"⚠️ 插件配置错误：{error_msg}\n请联系管理员检查配置。")
            return

        media_name = message.strip()
        user_id = event.get_sender_id()
        session_key = self._session_key(event)
        logger.info(f"用户 {user_id} 搜索影片: {media_name}")

        # 搜索影片
        try:
            movies = await self.api.search_media_info(media_name)
        except Exception as e:
            logger.error(f"搜索影片异常: {e}")
            yield self._md(event, "❌ 搜索服务暂时不可用，请稍后重试。")
            return

        if not movies:
            yield self._md(event, f"🔍 没有找到“{media_name}”，试试其他关键词吧。")
            return

        # 显示搜索结果
        result_lines = [
            f"🔎 搜索结果  ·  共 {len(movies)} 部",
            "────────────────",
            "",
        ]
        for i, movie in enumerate(movies, 1):
            title = movie.get("title", "未知")
            year = movie.get("year", "")
            media_type = movie.get("type", "未知")
            type_icon = "🎬" if media_type == "电影" else "📺"
            year_str = f" ({year})" if year else ""
            link = self._media_link(movie)
            result_lines.append(f"{i}. {type_icon} {title}{year_str}")
            if link:
                result_lines.append(f"   TMDB：{link}")

        result_lines.extend([
            "",
            "回复序号选择影片，回复 0 取消",
        ])
        result_text = "\n".join(result_lines)
        await self._set_user_state(
            session_key,
            {
                "movies": movies,
                "waiting_for": "movie",
                "expires_at": time.monotonic() + 60,
            },
        )
        yield self._md(event, result_text)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=999)
    async def on_selection_reply(self, event: AstrMessageEvent):
        session_key = self._session_key(event)
        state = await self._get_user_state(session_key)
        if not state:
            return
        if (event.message_str or "").lstrip().startswith(("/", "／")):
            return
        if time.monotonic() >= state.get("expires_at", 0):
            await self._clear_user_state(session_key)
            event.stop_event()
            yield self._md(event, "⏰ 操作超时，请重新搜索影片。")
            return

        user_input = (event.message_str or "").strip()
        event.stop_event()
        try:
            if state.get("waiting_for") == "season":
                keep_waiting = await self._process_season_selection(
                    event, session_key, user_input
                )
            else:
                keep_waiting = await self._process_movie_index_selection(
                    event, session_key, user_input, state.get("movies", [])
                )
            if keep_waiting:
                await self._refresh_user_state_expiry(session_key)
            else:
                await self._clear_user_state(session_key)
        except Exception as e:
            logger.exception(f"会话处理异常: {e}")
            await self._clear_user_state(session_key)
            yield self._md(event, "❌ 发生错误，请重新搜索影片。")

    async def _process_movie_index_selection(
        self,
        event: AstrMessageEvent,
        user_id: str,
        user_input: str,
        movies: list[dict],
    ) -> bool:
        """
        处理影片索引选择

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            user_input: 用户输入
            movies: 影片列表
        """

        try:
            index = int(user_input) - 1
        except ValueError:
            await event.send(self._md(event, "⚠️ 请输入有效的数字序号。"))
            return True

        # 用户取消
        if index == -1:
            await event.send(self._md(event, "❌ 已取消操作。"))
            return False

        # 验证索引范围
        if not (0 <= index < len(movies)):
            await event.send(self._md(event, "⚠️ 无效的序号，请输入列表中的数字。"))
            return True

        selected_movie = movies[index]
        logger.info(f"用户 {user_id} 选择了: {selected_movie.get('title')}")

        # 处理电视剧
        if selected_movie.get("type") == "电视剧":
            return await self._handle_tv_series_selection(event, user_id, selected_movie)
        else:
            # 处理电影订阅
            await self._subscribe_movie(event, selected_movie)
            return False

    async def _handle_tv_series_selection(
        self,
        event: AstrMessageEvent,
        user_id: str,
        movie: dict[str, Any],
    ) -> bool:
        """
        处理电视剧选择，获取并显示季度列表

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            movie: 电视剧信息
        """
        tmdb_id = movie.get("tmdb_id")
        if not tmdb_id or str(tmdb_id) in ("tv", "movie"):
            logger.warning(f"电视剧缺少有效的 TMDB ID: {tmdb_id}")
            await event.send(self._md(event, "❌ 影片信息不完整，无法获取季度信息。\n这可能是因为该影片缺少 TMDB 信息，建议直接尝试订阅。"))
            return False

        # 获取季度列表
        try:
            seasons = await self.api.list_all_seasons(tmdb_id)
        except Exception as e:
            logger.error(f"获取季度列表失败: {e}")
            await event.send(self._md(event, "❌ 无法获取季度信息，请稍后重试。"))
            return False

        if not seasons:
            await event.send(self._md(event, "❌ 没有找到可用的季度信息。"))
            return False

        # 显示季度列表
        title = movie.get("title", "未知")
        link = self._media_link(movie)
        year = movie.get("year", "")
        header = f"📺 {title} ({year})" if year else f"📺 {title}"
        result_lines = [
            header,
            "请选择要订阅的季度：",
        ]
        if link:
            result_lines.append(f"TMDB：{link}")
        result_lines.append("")
        for s in seasons:
            season_num = s.get("season_number", "?")
            season_name = s.get("name", "未命名")
            # 如果名称就是"第 X 季"，就不重复显示
            if season_name == f"第 {season_num} 季":
                result_lines.append(f"{season_num}. 第 {season_num} 季")
            else:
                result_lines.append(f"{season_num}. 第 {season_num} 季  ·  {season_name}")

        result_lines.extend([
            "",
            "回复季数订阅，回复 0 取消",
        ])
        result_text = "\n".join(result_lines)
        await event.send(self._md(event, result_text))

        # 更新用户状态
        await self._set_user_state(
            user_id,
            {
                "selected_movie": movie,
                "seasons": seasons,
                "waiting_for": "season",
                "expires_at": time.monotonic() + 60,
            },
        )

        return True

    async def _process_season_selection(
        self,
        event: AstrMessageEvent,
        user_id: str,
        user_input: str,
    ) -> bool:
        """
        处理季度选择

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            user_input: 用户输入
        """
        try:
            season_number = int(user_input)
        except ValueError:
            await event.send(self._md(event, "⚠️ 请输入有效的季数。"))
            return True

        # 用户取消
        if season_number == 0:
            await event.send(self._md(event, "❌ 已取消操作。"))
            return False

        # 获取用户状态
        state = await self._get_user_state(user_id)
        selected_movie = state.get("selected_movie", {})
        seasons = state.get("seasons", [])

        if not selected_movie or not seasons:
            await event.send(self._md(event, "❌ 会话已过期，请重新搜索。"))
            return False

        # 验证季度有效性
        valid_season = any(s.get("season_number") == season_number for s in seasons)
        if not valid_season:
            await event.send(self._md(event, "⚠️ 无效的季数，请从列表中选择。"))
            return True

        # 执行订阅
        logger.info(f"用户 {user_id} 订阅季度: {selected_movie.get('title')} 第{season_number}季")
        try:
            success = await self.api.subscribe_series(selected_movie, season_number)
        except Exception as e:
            logger.error(f"订阅电视剧失败: {e}")
            await event.send(self._md(event, "❌ 订阅服务暂时不可用，请稍后重试。"))
            return False

        if success:
            title = selected_movie.get("title")
            year = selected_movie.get("year")
            title_str = f"{title} ({year})" if year else title
            link = self._media_link(selected_movie)
            title_lines = [f"片名：{title_str}"]
            if link:
                title_lines.append(f"TMDB：{link}")
            result_text = "\n".join([
                "✅ 订阅成功",
                "────────────────",
                "类型：电视剧",
                *title_lines,
                f"季度：第 {season_number} 季",
            ])
        else:
            result_text = "❌ 订阅失败，请检查 MoviePilot 服务后重试。"

        await event.send(self._md(event, result_text))
        return False

    async def _subscribe_movie(
        self,
        event: AstrMessageEvent,
        movie: dict[str, Any],
    ) -> None:
        """
        订阅电影

        Args:
            event: 消息事件
            controller: 会话控制器
            movie: 电影信息
        """
        try:
            success = await self.api.subscribe_movie(movie)
        except Exception as e:
            logger.error(f"订阅电影失败: {e}")
            await event.send(self._md(event, "❌ 订阅服务暂时不可用，请稍后重试。"))
            return

        if success:
            title = movie.get("title")
            year = movie.get("year")
            title_str = f"{title} ({year})" if year else title
            link = self._media_link(movie)
            title_lines = [f"片名：{title_str}"]
            if link:
                title_lines.append(f"TMDB：{link}")
            result_text = "\n".join([
                "✅ 订阅成功",
                "────────────────",
                "类型：电影",
                *title_lines,
            ])
        else:
            result_text = "❌ 订阅失败，请检查 MoviePilot 服务后重试。"

        await event.send(self._md(event, result_text))

    async def _get_user_state(self, user_id: str) -> dict[str, Any]:
        """获取用户状态（线程安全）"""
        async with self.state_lock:
            return self.state.get(user_id, {}).copy()

    async def _set_user_state(self, user_id: str, state: dict[str, Any]) -> None:
        """设置用户状态（线程安全）"""
        async with self.state_lock:
            self.state[user_id] = state

    async def _clear_user_state(self, user_id: str) -> None:
        """清除用户状态（线程安全）"""
        async with self.state_lock:
            self.state.pop(user_id, None)

    async def _refresh_user_state_expiry(self, user_id: str) -> None:
        """无效输入后延长交互时限。"""
        async with self.state_lock:
            state = self.state.get(user_id)
            if state:
                state["expires_at"] = time.monotonic() + 60

    @filter.command("download")
    async def show_download_progress(self, event: AstrMessageEvent):
        """查看下载进度"""
        # 检查 API 配置
        valid, error_msg = self.api.validate_config()
        if not valid:
            yield self._md(event, f"⚠️ 插件配置错误：{error_msg}\n请联系管理员检查配置。")
            return

        try:
            progress_data = await self.api.get_download_progress()
        except Exception as e:
            logger.error(f"获取下载进度异常: {e}")
            yield self._md(event, "❌ 获取下载进度失败，请稍后重试。")
            return

        if progress_data is None:
            yield self._md(event, "❌ 无法连接到 MoviePilot 服务，请检查配置。")
            return

        if len(progress_data) == 0:
            yield self._md(event, "📭 当前没有正在下载的任务。")
            return

        # 用纯文本分组展示下载进度，避免 Markdown 在不同平台呈现不一致。
        result_lines = [
            f"📥 当前下载任务（{len(progress_data)} 个）",
            "────────────────",
            "",
        ]

        for index, task in enumerate(progress_data, 1):
            media = task.get("media", {})
            title = media.get("title") or task.get("title", "未知")
            season = media.get("season", "")
            episode = media.get("episode", "")
            progress = task.get("progress", 0)
            state = task.get("state", "unknown")
            speed = task.get("speed", "")

            # 进度条
            bar_length = 20
            filled = int(bar_length * progress / 100)
            bar = "█" * filled + "░" * (bar_length - filled)

            # 状态图标
            state_icon = {
                "downloading": "⬇️",
                "seeding": "✅",
                "paused": "⏸️",
                "error": "❌",
                "unknown": "❓",
            }.get(state.lower(), "❓")

            # 格式化任务信息
            task_line = f"{index}. {state_icon} {title}"
            if season:
                task_line += f" {season}"
            if episode:
                task_line += f" {episode}"

            result_lines.append(task_line)
            result_lines.append(f"   进度：{progress:.1f}%  [{bar}]")
            if speed:
                result_lines.append(f"   速度：{speed}")
            result_lines.append("")

        yield self._md(event, "\n".join(result_lines))
