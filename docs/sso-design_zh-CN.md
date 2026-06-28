# SSO 登入（OIDC）設計

> 路線圖 C-2（RBAC Phase 3）。本文是完整設計，分階段執行。
> 對齊現有程式碼：`apps/backend/app/core/auth.py`（`resolve_actor` 可插拔身分來源、
> `Role`、`require_role`）、`apps/backend/app/api/auth.py`（`/auth/status`、`/me`）、
> `apps/frontend_llmops/src/composables/useAuth.ts`（解鎖流程）、`deploy/nginx.conf`
> （單一 origin）。

## 0. 設計總則

- **多一個身分來源,不取代既有**:現在認證有三條（env admin token、operator token、
  open local-dev）。SSO 是**第四條**,接進同一個 `resolve_actor`,既有三條完全不動。
  人類走 SSO；機器 / CI / API 仍用 bearer token。
- **無狀態 session**:不引入伺服器端 session 儲存,也不加 `authlib`。用 **PyJWT 自簽的
  HS256 cookie** 當 session(`cryptography` + `httpx` + `PyJWT` 皆已具備)。後端可重啟 /
  多副本而不丟登入(只要共用 `LLMOPS_SESSION_SECRET`)——這點也替未來 C-3 HA 鋪路。
- **角色由 IdP 決定**:IdP 的 `groups` claim / email → `viewer | operator | admin`,
  沿用既有單調角色模型,登入後的授權邏輯**一行都不用改**。
- **預設關閉、零破壞**:未設 `LLMOPS_OIDC_*` 時 SSO 完全不存在,行為等同今天。

## 1. 現況與缺口

| 能力 | 現況 |
|---|---|
| 角色授權（viewer/operator/admin） | ✅ `require_role` + 單調 rank |
| 身分來源 | ⚠️ 只有「貼 bearer token」;無登入頁、無 session、無 IdP |
| 稽核歸屬 | ⚠️ operator 標籤,非真實 email |
| 離職撤權 | ❌ 手動撤 token |

缺的就是「**用公司帳號登入**」這層 —— 由 IdP 提供真實身分、group→角色、自動 deprovision。

## 2. OIDC 授權碼流程（Authorization Code + PKCE，手寫）

不依賴 authlib;用 `httpx`（discovery / token 交換）+ `PyJWT`（JWKS 驗 `id_token`）。

```
瀏覽器        後端 /api/auth/sso/*           IdP（OIDC Provider）
  │  點「以 SSO 登入」                          │
  ├─ GET /login?next=/cost ───────────────────►│
  │   產生 state+nonce+PKCE,簽進短效 tx cookie │
  │◄── 307 redirect 到 authorize_endpoint ─────┤
  ├──────────── 登入 + 同意 ───────────────────►│
  │◄──────── redirect 回 /callback?code&state ──┤
  ├─ GET /callback ────────────────────────────►│ token_endpoint(code+verifier)
  │   驗 state==tx、換 token、JWKS 驗 id_token   │◄── id_token + access_token
  │   claims→role;簽 session cookie             │
  │◄── 302 到 next,Set-Cookie: llmops_session ─┤
```

端點（皆在 `apps/backend/app/api/sso.py`,prefix `/api/auth/sso`,**免授權**——它們就是發證的）:

| 端點 | 行為 |
|---|---|
| `GET /login?next=` | 組 authorize URL(scope `openid email profile` + groups scope)、PKCE `code_challenge`、`state`/`nonce`;把 `{state,nonce,code_verifier,next}` 簽成短效(5 分鐘)`sso_tx` cookie;307 導去 IdP |
| `GET /callback?code=&state=` | 驗 `state==tx.state`;以 `code+code_verifier` 向 token_endpoint 換 token;用 discovery 的 `jwks_uri` 驗 `id_token`(簽章、`iss`、`aud==client_id`、`exp`、`nonce==tx.nonce`);取 `email`/`name`/groups → 映射 role;role 為 None 則 403;簽 `llmops_session` cookie;302 回 `next` |
| `POST /logout` | 清 `llmops_session` cookie |

- **Discovery**:`GET {issuer}/.well-known/openid-configuration`,取 `authorization_endpoint`/
  `token_endpoint`/`jwks_uri`,記憶體快取(TTL 1h)。
- **JWKS 驗章**:`PyJWT` 的 `PyJWKClient(jwks_uri)` 抓公鑰,`jwt.decode(..., audience=client_id,
  issuer=issuer)`。
- **`next` 防開放重導**:只接受以 `/` 開頭、非 `//` 的站內路徑,否則退回 `/`。

## 3. 角色映射（claims → Role）

純函式 `map_role(email, groups, settings) -> Role | None`,優先序:

1. `email ∈ LLMOPS_OIDC_ADMIN_EMAILS` → **admin**(逃生/首管,免設 group)。
2. groups 命中 `LLMOPS_OIDC_ADMIN_GROUPS` → admin;`…_OPERATOR_GROUPS` → operator;
   `…_VIEWER_GROUPS` → viewer(取命中的最高者)。
3. 都沒命中 → `LLMOPS_OIDC_DEFAULT_ROLE`(預設 `viewer`;設為空字串＝**未授權者拒登**)。

> groups claim 名稱可設(`LLMOPS_OIDC_GROUPS_CLAIM`,預設 `groups`;Entra 用 `roles` 或
> group id,Okta/Auth0 視設定)。純函式 → 100% 單元測試覆蓋。

## 4. Session（自簽 cookie）

- `llmops_session` = `PyJWT` HS256,payload `{sub, email, name, role, iat, exp}`,
  `exp = now + LLMOPS_SESSION_TTL`(預設 8h)。
- cookie 屬性:`HttpOnly`(JS 讀不到,防 XSS 竊取)、`SameSite=Lax`(IdP 導回是 top-level
  GET,Lax 會帶上)、`Secure`(當 `X-Forwarded-Proto=https` 時加)、`Path=/`。
- `sso_tx`(暫存 state/nonce/verifier)同樣 HS256 自簽、`Max-Age=300`、callback 後即清。
- 金鑰 `LLMOPS_SESSION_SECRET`:未設則用 `admin_token`(若有)派生,再無則啟動時隨機
  (隨機 → 重啟使登入失效,僅單機 dev 可接受;正式務必設)。

## 5. 接進 `resolve_actor`（唯一整合點）

新增來源,**置於既有規則之前但於 open-dev 之後**:

```
1. open local-dev（無 auth 且無 operator）→ (local-dev, admin)   # 不變
2. 有效 llmops_session cookie → (email 或 name, role)             # 新增
3. operator token（bearer）→ (label, role)                       # 不變
4. env admin token → (admin, admin)                              # 不變
5. 皆無 → 401
```

cookie 失效 / 過期 → 當作沒有,往下走(不直接報錯)。如此 **SSO 與 token 並存**:同一個
`require_role` 守衛、同一條稽核管線,SSO 使用者的稽核 actor 變成真實 email。

`/api/auth/status` 增 `sso_enabled`;`/me` 不變(cookie 也能解出身分)。

## 6. 前端

- `useAuth`:`refreshStatus` 讀 `sso_enabled`;**登入狀態以「`/me` 解得出 role」為準**,
  不再硬性要求本機有 token(因為 SSO 靠 HttpOnly cookie,JS 讀不到)。
- 解鎖對話框:`sso_enabled` 時顯示「**以 SSO 登入**」→ `window.location =
  '/api/auth/sso/login?next=' + 當前路徑`;token 輸入框保留為次選(機器/救援)。
- `logout`:`POST /api/auth/sso/logout` 後清本地狀態。
- `api.ts`:同源請求預設即帶 cookie(`credentials: 'same-origin'`),`/me`、所有 `/api`
  變更都會帶上 session;不需改 header 注入。

## 7. 設定（env，全部選用；未設 = SSO 關閉）

| env | 說明 |
|---|---|
| `LLMOPS_OIDC_ISSUER` | OIDC issuer（discovery base）。**設了才啟用 SSO** |
| `LLMOPS_OIDC_CLIENT_ID` / `_CLIENT_SECRET` | OAuth client |
| `LLMOPS_OIDC_REDIRECT_URL` | 預設由請求推導 `{scheme}://{host}/api/auth/sso/callback` |
| `LLMOPS_OIDC_SCOPES` | 預設 `openid email profile` |
| `LLMOPS_OIDC_GROUPS_CLAIM` | 預設 `groups` |
| `LLMOPS_OIDC_ADMIN_EMAILS` | 逗號清單,直接給 admin |
| `LLMOPS_OIDC_ADMIN_GROUPS` / `_OPERATOR_GROUPS` / `_VIEWER_GROUPS` | group→角色 |
| `LLMOPS_OIDC_DEFAULT_ROLE` | 未命中者的角色(預設 `viewer`;空＝拒登) |
| `LLMOPS_SESSION_SECRET` | session 簽章金鑰(正式必設) |
| `LLMOPS_SESSION_TTL` | session 秒數(預設 28800) |

## 8. 替代部署:forward-auth（oauth2-proxy）—— 選用

不想讓 app 內建 OIDC 的團隊,可在 nginx 前擺 **oauth2-proxy** 做 SSO,後端信任其注入的
`X-Auth-Request-Email` / `X-Auth-Request-Groups` header → 同一個 `map_role`。需用
`LLMOPS_TRUST_FORWARD_AUTH=true` 顯式開啟,且**只在受信任代理之後**啟用(否則 header 可偽造)。
本期以「app 內建 OIDC」為主,forward-auth 留作文檔指引 + 一個 env 開關。

## 9. 安全注意

- PKCE + `state`(防 CSRF)+ `nonce`(防 id_token 重放);`state`/`nonce`/`verifier` 不落
  伺服器,簽在短效 cookie。
- id_token 一律驗簽 + `iss`/`aud`/`exp`/`nonce`;access_token 不信任其內容。
- session cookie `HttpOnly`+`SameSite=Lax`(+`Secure` on https);TTL 限時。
- `next` 僅限站內路徑,擋開放重導。
- forward-auth header 預設不信任,需顯式開關 + 受信任代理。

---

## 分階段執行

- **Phase 1（後端 OIDC 核心）**:`oidc.py`(discovery 快取 + token 交換 + JWKS 驗證 +
  `map_role` + session/tx cookie 簽驗)、`api/sso.py`(login/callback/logout)、
  `resolve_actor` 加 cookie 來源、settings + `/auth/status` 加 `sso_enabled`、單元測試
  (map_role / cookie / callback state 驗證 / resolve 優先序,IdP 互動以 mock)。
- **Phase 2（前端）**:`useAuth` 以 `/me` 為登入準據 + SSO 按鈕 + logout;`api.ts` 帶
  cookie;i18n。
- **Phase 3（文檔 / 部署）**:`.env.example`、`deployment` 文檔(各 IdP 設定範例:Google /
  Entra / Okta)、forward-auth 指引、roadmap 標記。

> 先 Phase 1（讓「SSO 登入 + 角色映射」後端可動且可測），再接前端與文檔。
