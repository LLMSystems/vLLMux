# 評測資料集檢視器 — 設計（pre-run dataset inspector）

更新日期：2026-06-22
狀態：設計提案（開工中）

## 目標

評測目前 **跑之前看不到資料集本身**:catalog 只有 key/label/tier/metric,選 subset 只能在「進階」手打 raw JSON。要補:
1. **看細節**:每個資料集的筆數、subset/主題清單、metric、tags、說明。
2. **選主題**:用多選 UI 勾選 subset(MMLU 57 科、C-Eval 52、gsm8k main/socratic…),自動組成 `dataset_args.subset_list`,取代手打 JSON。
3. **預覽資料**:跑之前載入前 N 筆真實題目來看(question/choices/answer)。

post-run 的 sample 檢視(`/{run_id}/detail`、`/samples`)已完整,不動。

## 可行性（已驗證,evalscope 1.8.1）

- **metadata 來源**:`evalscope.api.registry.BENCHMARK_REGISTRY.get(name)` → `BenchmarkMeta`,有 `subset_list / default_subset / few_shot_num / metric_list / tags / pretty_name / description / eval_split / paper_url`。免載入、便宜。catalog 的 `key` 即 evalscope benchmark name。
- **預覽列**:
  ```python
  cfg = TaskConfig(model='preview', datasets=[key], limit=N,
                   dataset_args={key: {'subset_list': [subset], 'few_shot_num': 0}})
  ad = get_benchmark(key, cfg); dd = ad.load_dataset()
  ds = dd[subset]            # rows; len(ds) = 該 subset 筆數
  s = ds[i]                  # pydantic Sample: input / target / choices / metadata
  ```
  離線讀 `$MODELSCOPE_CACHE` cache。`few_shot_num=0` 讓 `input` 只含題目本身。
- `data_statistics` 為 None → 筆數在預覽某 subset 時用 `len(ds)` 回傳(不在列表頁全載)。

## API 設計（backend, read-only, 不需 admin）

`apps/backend/app/services/dataset_service.py`:
- `eval_dataset_meta(key) -> dict | None`:讀 registry,回 `{subsets, default_subset, few_shot_num, metric, tags, pretty_name, description, eval_split, paper_url}`。registry 沒有的 key 回 None(catalog 仍可用,只是無細節)。
- `eval_dataset_preview(key, subset, n=20) -> dict`:用上面方式載入,回 `{subset, count, rows:[{question, choices, target, metadata}], truncated}`。`few_shot_num=0`、`limit=n`。question 取 `input` 最後一則 user message 的文字。

`apps/backend/app/api/eval.py`:
- `GET /eval/datasets`:沿用 EVAL_CATALOG,但每筆 merge `eval_dataset_meta`(便宜)+ `cached` 狀態。
- `GET /eval/datasets/{key}/preview?subset=&n=`:回預覽列 + 該 subset 筆數。預設只對 **已下載** 的資料集開放(未下載會觸發下載,前端 disable + 提示)。

風險/邊界:
- 預覽務必走 `limit`,不整包載;大 subset 的 count 也只在開啟該 subset 時算。
- 未 cache 的資料集預覽會觸發下載 → 前端僅對 cached 啟用。
- key→registry name 對不上的(如自訂)優雅降級:無 subset picker / 無預覽,維持現有行為。
- 載入在 thread(同步 IO),別擋 event loop。

## 前端（EvalView.vue）

- 每個已選資料集:**subset 多選**(來自 meta.subsets;預設用 default_subset)→ 寫進 `dataset_args[key].subset_list`。保留 raw JSON 進階欄當逃生口。
- 每個資料集 chip:顯示 tags / metric / 一行說明;「**預覽**」按鈕(cached 才可點)開抽屜,表格列出前 N 筆 + 該 subset 筆數。
- limit 欄位:預覽載到 count 後可顯示「N / 總數」。

## 落地順序

1. backend service(meta + preview)+ 單元測試(registry 取值、preview 正規化;mock 或對 cached mmlu 跑)。
2. backend API 兩個端點。
3. 前端 subset picker + 預覽抽屜 + chip metadata。
4. vue-tsc + rebuild + live 驗證(對已下載的 mmlu/gsm8k/ceval)。
