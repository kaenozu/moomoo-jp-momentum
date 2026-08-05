# moomoo JP 米国株 OpenAPI 能力調査

調査日: 2026-08-05
対象: moomoo証券（Moomoo JP）の OpenAPI（Futu API 基盤, v10.9）を利用した米国株 SIMULATE 取引

## 1. 公式情報源

- Moomoo API 公式ドキュメント: https://openapi.moomoo.com/moomoo-api-doc/jp/ (Futu API Doc v10.9 と共通)
- Futu API Doc v10.9: https://openapi.futunn.com/futu-api-doc/en/
- moomoo証券プレスリリース（2026-03-13）: https://www.moomoo.com/jp/newsroom/moomoo-openapi
  - **moomoo証券は2026年3月13日より米国株APIトレード（Moomoo API）の提供を開始**
  - リアルタイムデータ取得・自動売買・バックテスト・ペーパートレード（模擬取引）が可能
- moomoo JP ヘルプ: micro米国株（端株） https://www.moomoo.com/jp/support/topic7_361
- moomoo JP ヘルプ: 米国株・ETF手数料 https://www.moomoo.com/jp/support/topic7_184

## 2. 重要: 既存READMEの制約認識との関係

既存READMEでは「moomoo JP / FUTUJP ではOpenAPI経由の日本株SIMULATE注文が利用できない可能性が高い」と記録されている。
これは**日本株**の話であり、**米国株は2026年3月よりAPIトレード対応**が開始された。
本調査の対象は米国株のみであり、日本株のAPI注文は本プロジェクトの対象外（既存方針を維持）。

## 3. 能力マトリクス

| 能力 | 公式対応 | Moomoo JP口座 | SIMULATE | REAL | ローカル実機確認 | 実装方針 |
|------|---------|--------------|----------|------|----------------|---------|
| 指値BUY | ✓ (OrderType.NORMAL) | ✓ (米国株API対応) | ✓ | 禁止 | 未確認 | LIMIT_SIMのみ使用 |
| 指値SELL | ✓ | ✓ | ✓ | 禁止 | 未確認 | 同上 |
| 成行注文 | ✓ | ✓ | ✓ | 禁止 | 未確認 | デフォルト無効 |
| 注文取消 | ✓ (modify_order) | ✓ | ✓ | 禁止 | 未確認 | ModifyOrderOp.CANCEL |
| 注文変更 | ✓ | ✓ | ✓ | 禁止 | 未確認 | 未約定のみ。MVPでは使用しない |
| 部分約定 | 対応（order pushのdealt_qty） | ✓ | ✓ | 禁止 | 未確認 | dealt_qtyで追跡 |
| 端株（fractional） | 公式API文書では確認できず | アプリ・PC版はmicro米国株対応。**APIチャネルでの対応は確認できず** | 不明 | 禁止 | 未確認 | **1株単位をデフォルト**。端株は推測で有効化しない |
| RTH | ✓ | ✓ | ✓ | 禁止 | 未確認 | MVPはRTHのみ |
| ETH | ✓（US margin paper口座のみ） | 不明 | ✓（限定的） | 禁止 | 未確認 | MVPでは無効 |
| OVERNIGHT | SIMULATE不可 | - | ✗ | 禁止 | 未確認 | 使用しない |
| order push callback | ✓ (update_order) | ✓ | ✓ | 禁止 | 未確認 | SIMULATEでも購読可。polling補完 |
| fill push callback | ✓ (update_order_fill) | ✓ | **SIMULATEでは非対応** | 禁止 | 未確認 | SIMULATEではdealt_qtyで代替 |
| 約定一覧照会 | ✓ | ✓ | **SIMULATEでは非対応** | 禁止 | 未確認 | SIMULATEでは不可。order queryのdealt_qtyで代替 |
| 口座一覧 | ✓ (get_acc_list) | ✓ | ✓ | 禁止 | 未確認 | SIMULATE US口座を明示選択 |
| 口座資金 | ✓ (accinfo_query) | ✓ | ✓ | 禁止 | 未確認 | read-onlyで確認 |
| ポジション | ✓ (get_position_list) | ✓ | ✓ | 禁止 | 未確認 | 実装 |
| 注文一覧 | ✓ (order_list_query) | ✓ | ✓ | 禁止 | 未確認 | 実装 |
| 取引ロック解除 | ペーパーでは不要 | ペーパー不要 | 不要 | 禁止 | 未確認 | 呼ばない |
| レート制限 | 15 req/30s / acc_id, 0.02s間隔 | ✓ | ✓ | 禁止 | 未確認 | rate limit queueで対応 |

## 4. アカウント選択（重要な設計判断）

公式ドキュメント（Q1, Q17）より:

- `OpenSecTradeContext(filter_trdmarket=TrdMarket.US, security_firm=SecurityFirm.FUTUINC)` でUS株の口座一覧を取得
- `trd_env == SIMULATE` かつ `sim_acc_type == STOCK_AND_OPTION`（US margin paper口座）または `STOCK`（US cash paper口座）を選択
- SIMULATE口座は broker 非依存（どの security_firm を渡しても全ペーパー口座が返る）
- **複数候補がある場合は自動で曖昧選択しない**。設定で `acc_id` を明示、または候補一覧を表示してユーザー確認
- REAL口座しか見つからない場合は停止（fail-closed）
- `acc_index` は口座追加・削除で変動するため、`acc_id` の使用が推奨

既存 `src/paper_trade.py` の問題点:
- `SecurityFirm.FUTUINC` を US にハードコード（moomoo JPは `SecurityFirm.MOOMOOJP` 系を要確認）
- `filter_trdmarket=TrdMarket.US` は正しい
- `get_acc_list` 未実装 → acc_id 未指定
- `modify_order(modify_order_op=1)` のmagic number → `ModifyOrderOp.CANCEL` を使うべき
- positions 未実装、partial fills 未対応、callback 未対応
- `INSERT OR REPLACE` 使用 → UPSERT（明示）へ変更すべき
- raw_response を無制限保存 → 制限すべき

## 5. SIMULATEペーパートレードの仕様（公式）

- 指値・成行のみ。有効期限は当日限り（DAYのみ）
- **約定照会・約定履歴・約定push（update_order_fill）はペーパーでは非対応**
  - → 注文照会の `dealt_qty` / `dealt_avg_price` と order push (update_order) で部分約定を追跡する
- 変更・取消は対応。成行・発動の無効化/削除は非対応
- 手数料照会・資金フロー照会はペーパーでは非対応
- unlock不要（ペーパー）
- US株ペーパーは RTH + ETH（margin paper口座のみ）。OVERNIGHTは不可。プレ/ポスト/オーバーナイトは非対応
- US margin paper口座: `sim_acc_type == STOCK_AND_OPTION`（株式+オプション）、`STOCK` は現金口座

## 6. 注文仕様

- 注文コード: `US.AAPL` 形式
- 数量単位: 株。端株のAPI対応は確認できず → **1株単位をデフォルト**
- 価格精度: US株は $1以上で小数2桁、$1未満で小数4桁
- 最小ティック: $1以上は$0.01、$1未満は$0.0001
- レート制限: 1口座あたり **15 req / 30秒**、連続リクエスト間隔 **0.02秒以上**
- 発注制限: moomoo US は 1注文あたり 500,000株 / $10,000,000
- moomoo JPのリスクコントロール: 市場価格から**40%以上乖離した新規買い指値は拒否**
- 一部銘柄は固定刻み（0.05, 0.5）の呼値
- `remark` フィールド（64バイトまで）で client order key を付与可能 → idempotencyに利用

## 7. moomoo JP の米国株手数料（公式, 2026-08時点）

### ベーシックコース（デフォルト）
- 取引手数料: 約定金額の **0.12%（税込0.132%）**、上限 20ドル（税込22ドル）、0.01ドル未満は0.01ドルとして扱い
- 現地清算費用: 当社負担

### アドバンスコース
- 取引手数料: 0.0049ドル/株（税込0.00539ドル/株）、最低 0.99ドル/注文、上限 約定代金の0.5%
- システム利用料: 0.005ドル/株、最低 1ドル/注文、上限 約定代金の0.5%
- 現地清算費用: 0.006ドル/株

### 端株（micro米国株）
- 1株未満の注文は、アドバンスコースでもベーシックコースの手数料が適用
- 端株取引はアプリ・PC版のみ（API対応は確認できず）

### 為替
- 為替手数料: 1ドルあたり **25銭**（自動両替・多通貨買付の定時両替）
- 円貨決済の買付余力拘束: 指値 = (指定価格×株数+手数料)×概算為替レート×105%

### 配当
- 配当課税: 自動源泉徴収（米国での10%等）

## 8. 設計への適用

### 8.1 SIMULATE adapter
- `TrdEnv.SIMULATE` を内部固定（外部引数で変更不可）
- `filter_trdmarket=TrdMarket.US`、moomoo JP対応の SecurityFirm を設定（FUTUINCはmoomoo US向け。JPは要確認）
- `get_acc_list` で SIMULATE US 口座を明示選択、複数候補は曖昧選択せず停止
- REAL口座のみなら停止
- 注文・約定・ポジション照会を実装、remoteをsource of truthにreconcile
- order push (update_order) を購読（SIMULATE対応）、fillはdealt_qtyで代替
- rate limit queue（15 req/30s, 0.02s間隔）
- `remark` に client order key を付与
- タイムアウト後の無条件再送禁止（先にorder存在確認）

### 8.2 コストモデル（config化）
```yaml
us_grid:
  costs:
    commission_mode: percentage
    commission_rate: 0.00132        # ベーシックコース 0.132% (税込)
    minimum_commission_usd: 0.01
    max_commission_rate: 0.011      # 上限22ドル相当（金額依存）
    spread_bps: 5
    slippage_bps: 5
    sell_regulatory_fee_enabled: true
    fx_cost_bps: 25                 # 1ドル=25銭（両替コスト、要換算）
```

### 8.3 端株
- APIの端株対応は公式文書で確認できず、実機SIMULATEでも未確認
- **バックテスト・注文とも1株単位をデフォルト**とする
- fractional対応は、公式仕様と実口座SIMULATEでの確認後にのみ有効化する

### 8.4 取引時間（US市場）
- MVPは RTH のみ（米国東部時間 9:30-16:00）
- プレ/ポスト/オーバーナイトは無効
- America/New_YorkタイムゾーンでDST・休場・早期終了を扱う

## 9. 未確認事項

- [ ] moomoo JP 口座の SecurityFirm 定数（MOOMOOJP? FUTUINC?）→ OpenD実機で確認が必要
- [ ] moomoo JP の SIMULATE US 口座が実際に存在するか
- [ ] 端株のAPI対応可否
- [ ] ETH のSIMULATE対応（margin paper口座のみ）→ MVPでは不要
- [ ] 手数料のAPI側での実際の適用（ペーパーでは手数料照会不可）
- [ ] `session` パラメータのSIMULATE挙動
- [ ] 為替の決済方式（外貨決済 vs 円貨決済）→ 両シナリオをバックテストで提示

## 10. 出典

- [Futu API Doc v10.9 - Transaction Related](https://openapi.futunn.com/futu-api-doc/en/qa/trade.html)
- [Futu API Doc v10.9 - Place Orders](https://openapi.futunn.com/futu-api-doc/en/trade/place-order.html)
- [moomoo証券 プレスリリース（2026-03-13）](https://www.moomoo.com/jp/newsroom/moomoo-openapi)
- [moomoo JP ヘルプ - micro米国株（端株）](https://www.moomoo.com/jp/support/topic7_361)
- [moomoo JP ヘルプ - 米国株・ETF手数料](https://www.moomoo.com/jp/support/topic7_184)
