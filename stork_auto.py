import asyncio
import random
import sys
import warnings
import aiofiles
import httpx
import urllib3
import botocore.session
from botocore.httpsession import URLLib3Session
from loguru import logger
import curl_cffi.requests
from pycognito import Cognito
from fake_useragent import UserAgent

# 禁用所有警告
warnings.filterwarnings('ignore')
# 禁用 urllib3 的不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 初始化日志记录
logger.remove()
logger.add(sys.stdout, format='<g>{time:YYYY-MM-DD HH:mm:ss:SSS}</g> | <c>{level}</c> | <level>{message}</level>')

# 创建一个信号量来控制并发访问
cognito_semaphore = asyncio.Semaphore(10)  # 允许最多10个并发请求

# 保存原始的send方法
original_send = URLLib3Session.send

# 定义一个全局的patched_send函数
def patched_send(self, request):
    self._verify = False
    return original_send(self, request)

# 只替换一次send方法
URLLib3Session.send = patched_send

# 创建UserAgent实例
ua = UserAgent()

async def get_cognito_tokens(email, password, proxy_url):
    async with cognito_semaphore:  # 使用信号量来控制并发
        try:
            # 定义同步认证函数
            def authenticate_sync(email, password, proxy_url):
                try:
                    # 使用pycognito进行认证
                    user_pool_id = 'ap-northeast-1_M22I44OpC'
                    client_id = '5msns4n49hmg3dftp2tp1t2iuh'
                    
                    cognito = Cognito(
                        user_pool_id=user_pool_id,
                        client_id=client_id,
                        user_pool_region='ap-northeast-1',
                        username=email
                    )
                    
                    # 设置代理
                    if proxy_url:
                        # 配置代理
                        boto_session = botocore.session.get_session()
                        boto_session.set_config_variable('proxies', {'https': proxy_url})
                        
                        # 创建新的客户端并替换cognito的客户端
                        cognito.client = boto_session.create_client(
                            'cognito-idp',
                            region_name='ap-northeast-1'
                        )
                        

                    
                    def add_custom_headers(request, **kwargs):
                        for key, value in {
                            'User-Agent': ua.chrome,
                            'Accept': 'application/json, text/javascript, */*; q=0.01',
                            'Accept-Language': 'en-US,en;q=0.9'
                        }.items():
                            request.headers.add_header(key, value)
                    
                    cognito.client.meta.events.register('request-created.*.*', add_custom_headers)
                    
                    # 进行认证
                    cognito.authenticate(password=password)
                    
                    return {
                        'refresh_token': cognito.refresh_token,
                        'id_token': cognito.id_token,
                        'access_token': cognito.access_token
                    }
                except Exception as e:
                    raise Exception(f"认证过程出错: {str(e)}")

            # 在线程中执行同步代码
            tokens = await asyncio.to_thread(authenticate_sync, email, password, proxy_url)
            return tokens
        except Exception as e:
            raise





async def save_refrral_code(referral_code):
    async with aiofiles.open('./referral_code', 'a') as writer:
        await writer.write(referral_code+"\n")
class StorkAuto:
    def __init__(self, acc: dict, headers: dict):
        self.acc = acc
        self.index = acc['index']
        self.email = acc['email']
        self.password = acc['password']
        self.proxy = acc['proxy']
        proxy_dict = {
            'http': self.proxy,
            'https': self.proxy
        }
        self.session = curl_cffi.requests.AsyncSession(headers=headers, proxies=proxy_dict, verify=False)

    async def update_token(self, token):
        self.session.headers.update({
            'Authorization': f'Bearer {token}'
        })

    async def loop_task(self):
        retry_count = 0
        max_retries = 3
        login_flag = True
        while True:
            try:
                if login_flag:
                # 1、用户认证
                    tokens = await get_cognito_tokens(self.email, self.password, self.proxy)
                    logger.success(f"账号 {self.index} - 登录成功！")
                    token = tokens['access_token']
                    await self.update_token(token)
                    login_flag = False
                retry_count = 0  # 重置重试计数

                # 2. 请求用户信息
                me_url = "https://app-api.jp.stork-oracle.network/v1/me"
                me_response = await self.session.get(me_url)
                me_data = me_response.json()

                if "data" in me_data:
                    user_id = me_data["data"].get("id", "")
                    email = me_data["data"].get("email", "")
                    referral_code = me_data["data"]["referral_code"]
                    await save_refrral_code(referral_code)
                    valid_count = me_data["data"]["stats"].get("stork_signed_prices_valid_count", "")
                    logger.info(f"账号 {self.index} - 用户信息获取成功: ID={user_id}, Email={email}, validCount={valid_count}")
                else:
                    logger.error(f"账号 {self.index} - 获取用户信息失败: {me_data}")
                    await asyncio.sleep(60)
                    continue

                for i in range(999):
                    try:
                        # 3. 获取价格信息
                        prices_url = "https://app-api.jp.stork-oracle.network/v1/stork_signed_prices"
                        prices_response = await self.session.get(prices_url)
                        if 'invalid token' in prices_response.text:
                            login_flag = True
                            logger.debug(f"账号 {self.index}, token过期：{prices_response.text}")
                            break

                        prices_data = prices_response.json()

                        if "data" not in prices_data:
                            logger.error(f"账号 {self.index} - 获取价格信息失败: {prices_data}")
                            await asyncio.sleep(60)
                            continue

                        # Get the first key-value pair from the data dictionary
                        first_pair = next(iter(prices_data["data"].items()))
                        asset_key, asset_data = first_pair
                        
                        timestamped_signature = asset_data.get("timestamped_signature", {})
                        msg_hash = timestamped_signature.get("msg_hash", "")

                        if not msg_hash:
                            logger.error(f"账号 {self.index} - 无法获取msg_hash")
                            await asyncio.sleep(60)
                            continue

                        logger.info(f"账号 {self.index} - 获取价格信息成功: msg_hash={msg_hash}")

                        # 4. 验证价格
                        validation_url = "https://app-api.jp.stork-oracle.network/v1/stork_signed_prices/validations"
                        validation_payload = {"msg_hash": msg_hash, "valid": True}
                        validation_response = await self.session.post(validation_url, json=validation_payload)
                        if 'invalid token' in validation_response.text:
                            logger.debug(f"账号 {self.index}, token过期：{validation_response.text}")
                            login_flag = True
                            break

                        validation_data = validation_response.json()
                        sleep_time = random.randint(280, 350)
                        if validation_data.get("message") == "ok":
                            logger.info(f"账号 {self.index} - 验证成功, 睡眠{sleep_time}秒等待下一次认证")
                        else:
                            logger.error(f"账号 {self.index} - 验证失败: {validation_data}")

                        await asyncio.sleep(sleep_time)

                    except Exception as e:
                        logger.error(f"账号 {self.index} - 循环中出错: {str(e)}")
                        await asyncio.sleep(60)

                # 5分钟后重新登录
                logger.info(f"账号 {self.index} - 开始新一轮")
                await asyncio.sleep(300)  # 添加延迟避免立即重试

            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"账号 {self.index} - 达到最大重试次数，等待较长时间后重试: {str(e)}")
                    sleep_time = random.randint(600, 900)
                    await asyncio.sleep(sleep_time)  # 15分钟后重试
                    retry_count = 0
                else:
                    logger.error(f"账号 {self.index} - 主循环出错 (重试 {retry_count}/{max_retries}): {str(e)}")
                    if 'Request not allowed due to WAF block' in str(e):
                        login_flag = True
                        sleep_time = random.randint(3600, 3800)
                        await asyncio.sleep(sleep_time)  # 1h后重试
                    elif random.random() < 0.3:  # 30% 的几率
                        extra_sleep = random.randint(200, 400)
                        # logger.info(f"账号 {self.index} - 随机触发额外睡眠 {extra_sleep} 秒")
                        await asyncio.sleep(extra_sleep)
                    else:
                        sleep_time = random.randint(100, 300)
                        await asyncio.sleep(sleep_time)  # 5分钟后重试


async def run(acc: dict):
    headers = {
        'scheme': 'https',
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8',
        'Origin': 'chrome-extension://knnliglhgkmlblppdejchidfihjnockl',
        # 'Authorization': f'Bearer {acc["token"]}'
    }
    stork = StorkAuto(acc, headers)
    await stork.loop_task()


async def main():

    accs = []
    # 格式：email----password----socks5代理
    async with aiofiles.open('./acc', 'r', encoding='utf-8') as file:
        for index, line in enumerate(await file.readlines()):
            acc = {
                'index': index
            }
            parts = line.strip().split('----')
            acc['email'] = parts[0]
            acc['password'] = parts[1]
            acc['proxy'] = parts[2]
            accs.append(acc)

    logger.info(f'一共有{len(accs)} 个账号')

    # 创建任务列表并运行
    tasks = [run(acc) for acc in accs]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    logger.info('🚀 [ILSH] STORK v1.0 | Airdrop Campaign Live')
    logger.info('🌐 ILSH Community: t.me/ilsh_auto')
    logger.info('🐦 X(Twitter): https://x.com/hashlmBrian')
    logger.info('☕ Pay me Coffe：USDT（TRC20）:TAiGnbo2isJYvPmNuJ4t5kAyvZPvAmBLch')
    asyncio.run(main())
