import traceback
from typing import List
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import httpx

class MoviepilotApi:
    def __init__(self, config: dict):
        self.base_url = config.get('mp_url')
        self.mp_username = config.get('mp_username')
        self.mp_password = config.get('mp_password')
        print(self.mp_username)        

    async def _get_mp_token(self) -> str | None:
        _api_path = "/api/v1/login/access-token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json"
        }
        # 构建表单数据
        form_data = {
            "username": self.mp_username,
            "password": self.mp_password,
        }

        if self.mp_password is None:
            logger.error("moviepilot的密码不能为空")
            return ""
        else:
            # 发送 POST 请求并传递表单数据
            data = await self._request(
                url=self.base_url + _api_path,
                method="POST-DATA",
                headers=headers,
                data=form_data
            )
            return data.get("access_token", None) if data else None

    async def _get_headers(self) -> dict[str, str] | None:
        _token = await self._get_mp_token()
        if _token:
            return {
                "Authorization": f"Bearer {_token}",
                'User-Agent': "nonebot2/0.0.1"
            }
        else:
            logger.error("访问MoviePilot失败，请确认密码或者是否开启了两步验证")
            return

    async def search_media_info(self, media_name: str) -> dict | None:
        _api_path = f"/api/v1/media/search?title={media_name}"
        try:
            return await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=await self._get_headers()
            )
        except Exception as e:
            logger.error(f"Error searching movies: {e}\n{traceback.format_exc()}")
            return None

    async def list_all_seasons(self, tmdbid: str) -> dict | None:
        _api_path = f"/api/v1/tmdb/seasons/{tmdbid}"
        try:
            return await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=await self._get_headers()
            )
        except Exception as e:
            logger.error(f"Error listing seasons: {e}")
            return None

    async def subscribe_movie(self, movie: dict) -> bool:
        _api_path = "/api/v1/subscribe/"
        body = {
            "name": movie['title'],
            "tmdbid": movie['tmdb_id'],
            "type": "电影"
        }
        try:
            response = await self._request(
                url=self.base_url + _api_path,
                method="POST-JSON",
                headers=await self._get_headers(),
                data=body
            )
            logger.info(response)
            return response.get("success", False) if response else False
        except Exception as e:
            logger.error(f"Error subscribing to movie: {e}")
            return False

    async def subscribe_series(self, movie: dict, season: int) -> bool:
        _api_path = "/api/v1/subscribe/"
        body = {
            "name": movie['title'],
            "tmdbid": movie['tmdb_id'],
            "season": season
        }
        try:
            response = await self._request(
                url=self.base_url + _api_path,
                method="POST-JSON",
                headers=await self._get_headers(),
                data=body
            )
            return response.get("success", False) if response else False
        except Exception as e:
            logger.error(f"Error subscribing to series: {e}")
            return False

    async def _request(
            self,
            url,
            method="GET",
            headers=None,
            data=None
    ) -> List | None:

        if headers is None:
            headers = {'user-agent': 'nonebot2/0.0.1'}
        timeout = httpx.Timeout(120.0, read=120.0)

        logger.info(f"""
                url: {url}
                method = {method}
                headers = {headers}
                data = {data}
                """)

        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                r = await client.get(url, headers=headers)
            elif method == "POST-JSON":
                r = await client.post(url, headers=headers, json=data)
            elif method == "POST-DATA":
                r = await client.post(url, headers=headers, data=data)
            else:
                return

            if r.status_code != 200:
                logger.error(f"{r.status_code} 请求错误\n{r}")
            else:
                return r.json()

    async def get_download_progress(self) -> List[dict] | None:
        """获取下载进度
        Returns:
            List[dict] | None: 返回下载任务列表，每个任务包含以下字段：
            - media: dict 媒体信息
                - title: str 中文标题
                - type: str 类型（电影/电视剧）
            - progress: float 下载进度（百分比）
            - state: str 下载状态
        """
        _api_path = "/api/v1/download/"
        try:
            headers = await self._get_headers()
            if not headers:
                logger.error("获取认证头失败")
                return None
                
            data = await self._request(
                url=self.base_url + _api_path,
                method="GET",
                headers=headers
            )
            
            if not data:
                logger.info("当前没有正在下载的任务")
                return []
                
            return data
            
        except Exception as e:
            logger.error(f"获取下载进度失败: {e}")
            return None




