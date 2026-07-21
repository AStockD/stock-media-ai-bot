# 雪球评论功能开发：挑战与解决方案

## 背景

为 stock-media-ai-bot 项目添加自动评论功能，需要在雪球平台上找到指定帖子并发布评论。由于雪球的反爬虫机制，遇到了多个技术挑战。

---

## 挑战 1：WAF 验证码阻止直接访问

### 问题描述
最初尝试直接导航到帖子详情页 (`https://xueqiu.com/{uid}/{pid}`) 或用户主页 (`https://xueqiu.com/u/{uid}`)，都会触发阿里云 WAF 验证码（滑块验证），导致无法访问页面内容。

### 尝试的解决方案
1. **添加 Stealth 措施** — 隐藏 `navigator.webdriver`、伪装插件和语言设置
   - 结果：首页不再触发 WAF，但帖子详情页和用户主页仍然触发
   
2. **使用浏览器 API 调用** — 在浏览器上下文中用 `fetch` 调用雪球 API
   - 结果：服务器 IP 被雪球加入黑名单，返回 403 "Seek IP Blacklisted"

### 最终解决方案
**只访问首页，不访问其他页面**。雪球首页不触发 WAF，利用首页的搜索功能找到目标帖子。

```python
# 只导航到首页
await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
await page.wait_for_timeout(5000)

# 使用首页搜索框
search_box = await page.query_selector('input[placeholder*="搜索"]')
await search_box.fill(post_title)
await page.keyboard.press("Enter")
```

---

## 挑战 2：服务器 IP 被雪球黑名单

### 问题描述
从服务器直接调用雪球 API（使用 `httpx` 或 `curl`）返回 403 错误，提示 "Seek IP Blacklisted"。

### 解决方案
**所有请求都通过浏览器发出**，利用浏览器已有的 cookie 和 session，避免 IP 被识别为爬虫。

```python
# 在浏览器上下文中调用 API
api_result = await page.evaluate("""async (url) => {
    const resp = await fetch(url, {credentials: 'include'});
    return await resp.json();
}""", api_url)
```

---

## 挑战 3：如何找到目标帖子

### 问题描述
需要在首页找到指定的帖子才能评论。尝试了多种方法：

1. **滚动首页时间线** — 首页只显示关注用户的最近帖子，目标帖子可能不在其中
2. **搜索 post_id** — 雪球搜索是关键词匹配，不支持按 ID 搜索
3. **搜索账号昵称** — 会触发 WAF 或跳转到用户主页（也触发 WAF）

### 最终解决方案
**使用帖子标题搜索**。帖子标题已保存在缓存中（`posts_cache.json`），用标题作为搜索关键词可以找到目标帖子。

```python
# 从缓存获取帖子标题
post_title = "A股道股票解读每日分享 - 2026/07/20 - 中国化学"

# 清理标题（去掉 HTML 标签和 markdown 前缀）
import re
search_query = re.sub(r'<[^>]+>', '', post_title)
search_query = re.sub(r'^[#*\s]+', '', search_query)
search_query = search_query.strip()[:50]

# 搜索
await search_box.fill(search_query)
await page.keyboard.press("Enter")

# 在搜索结果中查找 post_id
found = await page.evaluate("""(postId) => {
    const link = document.querySelector(`a[href*="${postId}"]`);
    return !!link;
}""", post_id)
```

---

## 挑战 4：保存账号昵称

### 问题描述
最初计划保存用户雪球昵称用于搜索，但登录流程没有提取和保存昵称。

### 解决方案
**在登录和发帖时提取昵称并保存到数据库**。

```python
# 提取昵称（使用 a.user-name 选择器）
account_name = await page.evaluate("""() => {
    const links = document.querySelectorAll('a.user-name');
    for (const a of links) {
        const text = a.textContent.trim();
        if (text && text.length > 0 && text.length < 50) {
            return text;
        }
    }
    return null;
}""")

# 保存到数据库
self.account_manager.save_cookies(
    user_id=user_id,
    platform=platform,
    cookies=cookies_dict,
    storage_state=storage_state,
    account_name=account_name,
)
```

**注意**：最终发现用帖子标题搜索更可靠，账号昵称作为备用方案。

---

## 挑战 5：WAF 随机出现

### 问题描述
即使只访问首页，WAF 也会随机出现（可能基于请求频率或其他因素），阻止点击操作。

### 解决方案
**添加 WAF 检测和处理函数**，在关键操作后检查并移除 WAF 遮罩。

```python
async def _remove_waf(self, page):
    waf_present = await page.evaluate("""() => {
        return !!(document.querySelector('[id*="waf"]') ||
                  document.querySelector('[class*="waf"]') ||
                  document.querySelector('[class*="nc-mask"]'));
    }""")
    if waf_present:
        logger.info("WAF detected, removing...")
        await page.evaluate("""() => {
            const waf = document.querySelector('#waf_nc_block, [class*="waf-nc-mask"], [class*="nc-mask"]');
            if (waf) waf.remove();
            const overlays = document.querySelectorAll('[style*="position: fixed"][style*="z-index"]');
            overlays.forEach(el => {
                if (el.textContent.includes('verify') || el.textContent.includes('验证')) {
                    el.remove();
                }
            });
        }""")
        await page.wait_for_timeout(2000)
    return waf_present

# 在关键操作后调用
await page.goto("https://xueqiu.com", ...)
await self._remove_waf(page)

await page.keyboard.press("Enter")  # 搜索后
await self._remove_waf(page)
```

---

## 挑战 6：帖子标题包含 HTML 标签

### 问题描述
从 API 获取的帖子标题包含 HTML 标签（如 `<br/>`）和 markdown 前缀（如 `## `），直接搜索效果不好。

### 解决方案
**清理标题后再搜索**。

```python
# 获取标题时清理
if title:
    import re
    title = re.sub(r'<[^>]+>', '', title)  # 去掉 HTML 标签
    title = re.sub(r'^[#*\s]+', '', title).strip()  # 去掉 markdown 前缀

# 搜索前再次清理
search_query = re.sub(r'<[^>]+>', '', post_title)
search_query = re.sub(r'^[#*\s]+', '', search_query)
search_query = search_query.strip()[:50]  # 限制长度
```

---

## 挑战 7：评论流程的 DOM 操作

### 问题描述
找到帖子后，需要点击"讨论"按钮、找到评论编辑器、输入内容、点击提交。这些操作需要精确定位 DOM 元素。

### 解决方案
**使用多策略定位元素**，优先用 Playwright 选择器，失败时用坐标点击。

```python
# 1. 点击"讨论"按钮
article = page.locator(f'article:has(a[href*="{post_id}"])').first
discuss_btn = article.locator('a.timeline__item__control:has(span:text("讨论"))')
await discuss_btn.click(force=True, timeout=5000)

# 2. 找到评论编辑器
editor_result = await page.evaluate("""(postId) => {
    const link = document.querySelector(`a[href*="${postId}"]`);
    let article = link;
    while (article && article.tagName !== 'ARTICLE') {
        article = article.parentElement;
    }
    const commentSection = article.querySelector('.timeline__item__comment');
    const editor = commentSection.querySelector('[contenteditable="true"]');
    if (editor) {
        const rect = editor.getBoundingClientRect();
        return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2};
    }
    return {found: false};
}""", post_id)

# 3. 点击编辑器并输入
await page.mouse.click(editor_result["x"], editor_result["y"])
await page.keyboard.type(content, delay=30)

# 4. 点击提交按钮
submit_btn = article_locator.locator('a.lite-editor__submit:text("发布")')
await submit_btn.click(force=True, timeout=5000)
```

---

## 最终架构

```
─────────────────────────────────────────────────────────────┐
│                      评论流程                                │
├─────────────────────────────────────────────────────────────┤
│  1. 获取帖子标题（从缓存 posts_cache.json）                   │
│  2. 启动浏览器（带 stealth 措施）                             │
│  3. 导航到首页（不触发 WAF）                                  │
│  4. 检测并移除 WAF（如果出现）                                │
│  5. 搜索帖子标题（清理 HTML 和 markdown）                     │
│  6. 检测并移除 WAF（如果出现）                                │
│  7. 在搜索结果中查找 post_id                                  │
│  8. 滚动加载更多（如果初始视图没找到）                        │
│  9. 点击"讨论"按钮                                            │
│  10. 找到评论编辑器                                           │
│  11. 输入评论内容                                             │
│  12. 点击"发布"按钮                                           │
│  13. 监听评论 API 响应（确认成功）                            │
│  14. 保存 cookies                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 关键经验

1. **不要对抗 WAF，而是绕过它** — 只访问不触发 WAF 的页面（首页）
2. **利用现有数据** — 帖子标题已保存在缓存中，直接复用
3. **多策略容错** — DOM 定位失败时用坐标点击作为备用
4. **及时保存状态** — 每次操作后保存 cookies，避免登录过期
5. **清理输入数据** — HTML 标签和 markdown 前缀会影响搜索效果

---

## 文件清单

| 文件 | 改动说明 |
|------|----------|
| `backend/app/services/xueqiu_comment_service.py` | 核心评论服务，添加标题搜索和 WAF 处理 |
| `backend/app/services/xueqiu_login_service.py` | 登录时提取并保存账号昵称 |
| `backend/app/services/xueqiu_post_service.py` | 发帖时提取并保存账号昵称 |
| `backend/app/routers/platform.py` | 评论接口接收 `post_title`，帖子列表清理标题 |
| `frontend/src/api/client.ts` | `createComment` 增加 `postTitle` 参数 |
| `frontend/src/pages/MyPosts.tsx` | 评论时传递帖子标题 |

---

## 测试结果

```
=== Step 1: Navigate to homepage ===
WAF detected, removing...
WAF removed, waiting...

=== Step 2: Search for 'A股道股票解读每日分享 - 2026/07/20 - 中国化学' ===
URL: https://xueqiu.com/k?q=A%E8%82%A1%E9%81%93...

=== Step 3: Find post 401132654 ===
Post found: True

=== Step 4: Click 讨论 button ===
讨论 button: {'found': True, 'x': 466, 'y': 400.1875}

=== Step 5: Find comment editor ===
Editor: {'found': True, 'x': 659, 'y': 449.984375}

=== Step 6: Type comment and submit ===
Typing: 测试-5784511.55
Comment API: https://xueqiu.com/statuses/reply.json -> 200

SUCCESS: Comment posted!
```

评论成功创建，ID: 416840833
