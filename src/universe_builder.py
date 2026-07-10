"""
ユニバース構築モジュール

ファイルパス: src/universe_builder.py
何をするか: 銘柄ユニバースの生成・拡張・マージ・分類を行う
なぜ存在するか: 分析対象を200〜500銘柄へ拡張し、セクター分散されたユニバースを作るため
関連ファイル: data/symbols.json, screener.py, daily_update.py
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# ユニバース分類ルール
# ──────────────────────────────────────────

MIN_TRADE_PRICE = 500
DEFAULT_MAX_TRADE_PRICE = 20000

ROLE_TRADE_CANDIDATE = "trade_candidate"
ROLE_WATCH_ONLY = "watch_only"
ROLE_BENCHMARK = "benchmark"
ROLE_EXCLUDED = "excluded"
ROLE_ETF_CANDIDATE = "etf_candidate"

TYPE_STOCK = "stock"
TYPE_ETF = "etf"

# ──────────────────────────────────────────
# デフォルト候補銘柄リスト（東証プライム中心）
# 各銘柄の price は概算（参考値）。実際の close とは異なる
# ──────────────────────────────────────────

DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    # ── 電気機器 ──
    {"code": "JP.6758", "name": "ソニーグループ", "sector": "電気機器", "estimated_price": 15000},
    {"code": "JP.6501", "name": "日立製作所", "sector": "電気機器", "estimated_price": 16000},
    {"code": "JP.6954", "name": "ファナック", "sector": "電気機器", "estimated_price": 4500},
    {"code": "JP.6702", "name": "富士通", "sector": "電気機器", "estimated_price": 2800},
    {"code": "JP.7974", "name": "任天堂", "sector": "電気機器", "estimated_price": 8500},
    {"code": "JP.6861", "name": "キーエンス", "sector": "電気機器", "estimated_price": 70000},
    {"code": "JP.6503", "name": "三菱電機", "sector": "電気機器", "estimated_price": 2500},
    {"code": "JP.6752", "name": "パナソニックHD", "sector": "電気機器", "estimated_price": 1500},
    {"code": "JP.7731", "name": "ニコン", "sector": "電気機器", "estimated_price": 1800},
    {"code": "JP.7751", "name": "キャノン", "sector": "電気機器", "estimated_price": 4500},
    {"code": "JP.6701", "name": "NEC", "sector": "電気機器", "estimated_price": 12000},
    {"code": "JP.6762", "name": "TDK", "sector": "電気機器", "estimated_price": 8000},
    {"code": "JP.6971", "name": "京セラ", "sector": "電気機器", "estimated_price": 2000},
    {"code": "JP.6981", "name": "村田製作所", "sector": "電気機器", "estimated_price": 3000},
    {"code": "JP.6857", "name": "アドバンテスト", "sector": "電気機器", "estimated_price": 6000},
    {"code": "JP.6723", "name": "ルネサスエレクトロニクス", "sector": "電気機器", "estimated_price": 2500},
    {"code": "JP.6645", "name": "オムロン", "sector": "電気機器", "estimated_price": 6000},
    {"code": "JP.6594", "name": "ニデック", "sector": "電気機器", "estimated_price": 7000},
    {"code": "JP.6920", "name": "レーザーテック", "sector": "電気機器", "estimated_price": 40000},
    {"code": "JP.6753", "name": "シャープ", "sector": "電気機器", "estimated_price": 1000},
    {"code": "JP.6770", "name": "アルプスアルパイン", "sector": "電気機器", "estimated_price": 1500},
    {"code": "JP.6841", "name": "横河電機", "sector": "電気機器", "estimated_price": 4000},
    {"code": "JP.7741", "name": "HOYA", "sector": "電気機器", "estimated_price": 35000},
    {"code": "JP.4543", "name": "テルモ", "sector": "精密機器", "estimated_price": 5000},
    {"code": "JP.7733", "name": "オリンパス", "sector": "精密機器", "estimated_price": 2800},
    {"code": "JP.7735", "name": "SCREENホールディングス", "sector": "精密機器", "estimated_price": 15000},
    {"code": "JP.8035", "name": "東京エレクトロン", "sector": "精密機器", "estimated_price": 35000},
    {"code": "JP.6146", "name": "ディスコ", "sector": "精密機器", "estimated_price": 55000},
    # ── 銀行 ──
    {"code": "JP.8306", "name": "三菱UFJFG", "sector": "銀行", "estimated_price": 1800},
    {"code": "JP.8316", "name": "三井住友FG", "sector": "銀行", "estimated_price": 10000},
    {"code": "JP.8411", "name": "みずほFG", "sector": "銀行", "estimated_price": 3500},
    {"code": "JP.7186", "name": "コンコルディアFG", "sector": "銀行", "estimated_price": 800},
    {"code": "JP.8308", "name": "りそなHD", "sector": "銀行", "estimated_price": 1000},
    {"code": "JP.8331", "name": "千葉銀行", "sector": "銀行", "estimated_price": 1300},
    {"code": "JP.8355", "name": "静岡銀行", "sector": "銀行", "estimated_price": 1500},
    {"code": "JP.8381", "name": "ふくおかFG", "sector": "銀行", "estimated_price": 3000},
    {"code": "JP.8527", "name": "愛知銀行", "sector": "銀行", "estimated_price": 3000},
    {"code": "JP.8377", "name": "北國銀行", "sector": "銀行", "estimated_price": 3500},
    {"code": "JP.7189", "name": "西日本FG", "sector": "銀行", "estimated_price": 2500},
    {"code": "JP.7337", "name": "ひろぎんHD", "sector": "銀行", "estimated_price": 1200},
    {"code": "JP.8550", "name": "南都銀行", "sector": "銀行", "estimated_price": 2500},
    # ── 情報・通信 ──
    {"code": "JP.9432", "name": "NTT", "sector": "情報・通信", "estimated_price": 150},
    {"code": "JP.9433", "name": "KDDI", "sector": "情報・通信", "estimated_price": 4500},
    {"code": "JP.9434", "name": "ソフトバンク", "sector": "情報・通信", "estimated_price": 2000},
    {"code": "JP.9984", "name": "ソフトバンクグループ", "sector": "情報・通信", "estimated_price": 9000},
    {"code": "JP.9437", "name": "NTTドコモ", "sector": "情報・通信", "estimated_price": 4000},
    {"code": "JP.4689", "name": "ヤフー", "sector": "情報・通信", "estimated_price": 400},
    {"code": "JP.4751", "name": "サイバーエージェント", "sector": "情報・通信", "estimated_price": 1000},
    {"code": "JP.4755", "name": "楽天グループ", "sector": "情報・通信", "estimated_price": 800},
    {"code": "JP.3769", "name": "GMOペイメントゲートウェイ", "sector": "情報・通信", "estimated_price": 8000},
    {"code": "JP.2127", "name": "日本M&AセンターHD", "sector": "情報・通信", "estimated_price": 800},
    {"code": "JP.3635", "name": "コーエーテクモHD", "sector": "情報・通信", "estimated_price": 6000},
    {"code": "JP.3938", "name": "LINE", "sector": "情報・通信", "estimated_price": 1000},
    {"code": "JP.9684", "name": "スクウェア・エニックスHD", "sector": "情報・通信", "estimated_price": 6000},
    {"code": "JP.4324", "name": "電通グループ", "sector": "情報・通信", "estimated_price": 4000},
    # ── 輸送用機器 ──
    {"code": "JP.7203", "name": "トヨタ自動車", "sector": "輸送用機器", "estimated_price": 3000},
    {"code": "JP.7267", "name": "本田技研工業", "sector": "輸送用機器", "estimated_price": 1700},
    {"code": "JP.7201", "name": "日産自動車", "sector": "輸送用機器", "estimated_price": 500},
    {"code": "JP.7269", "name": "スズキ", "sector": "輸送用機器", "estimated_price": 1800},
    {"code": "JP.7270", "name": "SUBARU", "sector": "輸送用機器", "estimated_price": 2400},
    {"code": "JP.7259", "name": "アイシン", "sector": "輸送用機器", "estimated_price": 5000},
    {"code": "JP.7205", "name": "いすゞ自動車", "sector": "輸送用機器", "estimated_price": 2000},
    {"code": "JP.7211", "name": "三菱自動車", "sector": "輸送用機器", "estimated_price": 500},
    {"code": "JP.7261", "name": "マツダ", "sector": "輸送用機器", "estimated_price": 1800},
    {"code": "JP.6201", "name": "豊田自動織機", "sector": "輸送用機器", "estimated_price": 14000},
    {"code": "JP.6902", "name": "デンソー", "sector": "輸送用機器", "estimated_price": 2500},
    {"code": "JP.5801", "name": "古河電気工業", "sector": "輸送用機器", "estimated_price": 3000},
    {"code": "JP.5802", "name": "住友電気工業", "sector": "輸送用機器", "estimated_price": 2500},
    # ── 卸売業 ──
    {"code": "JP.8058", "name": "三菱商事", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8001", "name": "伊藤忠商事", "sector": "卸売業", "estimated_price": 6000},
    {"code": "JP.8031", "name": "三井物産", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8053", "name": "住友商事", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8002", "name": "丸紅", "sector": "卸売業", "estimated_price": 2500},
    {"code": "JP.2768", "name": "双日", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8136", "name": "サンリオ", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8088", "name": "岩谷産業", "sector": "卸売業", "estimated_price": 5000},
    {"code": "JP.8015", "name": "豊田通商", "sector": "卸売業", "estimated_price": 8000},
    {"code": "JP.8050", "name": "セイコーグループ", "sector": "卸売業", "estimated_price": 3000},
    {"code": "JP.8158", "name": "ソーダニッカ", "sector": "卸売業", "estimated_price": 1500},
    # ── 化学 ──
    {"code": "JP.4063", "name": "信越化学工業", "sector": "化学", "estimated_price": 5500},
    {"code": "JP.3407", "name": "旭化成", "sector": "化学", "estimated_price": 1200},
    {"code": "JP.4188", "name": "三菱ケミカルG", "sector": "化学", "estimated_price": 800},
    {"code": "JP.4921", "name": "ファンケル", "sector": "化学", "estimated_price": 2500},
    {"code": "JP.4452", "name": "花王", "sector": "化学", "estimated_price": 6000},
    {"code": "JP.4901", "name": "富士フイルムHD", "sector": "化学", "estimated_price": 10000},
    {"code": "JP.4042", "name": "東ソー", "sector": "化学", "estimated_price": 2500},
    {"code": "JP.4183", "name": "三井化学", "sector": "化学", "estimated_price": 3500},
    {"code": "JP.4005", "name": "住友化学", "sector": "化学", "estimated_price": 400},
    {"code": "JP.3405", "name": "クラレ", "sector": "化学", "estimated_price": 2000},
    {"code": "JP.4612", "name": "日本ペイントHD", "sector": "化学", "estimated_price": 1200},
    {"code": "JP.4631", "name": "DIC", "sector": "化学", "estimated_price": 3000},
    {"code": "JP.6988", "name": "日東電工", "sector": "化学", "estimated_price": 5500},
    # ── 医薬品 ──
    {"code": "JP.4502", "name": "アステラス製薬", "sector": "医薬品", "estimated_price": 1500},
    {"code": "JP.4503", "name": "アステラス製薬", "sector": "医薬品", "estimated_price": 4000},
    {"code": "JP.4523", "name": "エーザイ", "sector": "医薬品", "estimated_price": 5000},
    {"code": "JP.4568", "name": "第一三共", "sector": "医薬品", "estimated_price": 5000},
    {"code": "JP.4519", "name": "中外製薬", "sector": "医薬品", "estimated_price": 5000},
    {"code": "JP.4506", "name": "大日本住友製薬", "sector": "医薬品", "estimated_price": 500},
    {"code": "JP.4571", "name": "ナノキャリア", "sector": "医薬品", "estimated_price": 300},
    {"code": "JP.4516", "name": "日本新薬", "sector": "医薬品", "estimated_price": 5000},
    {"code": "JP.4521", "name": "科研製薬", "sector": "医薬品", "estimated_price": 4000},
    {"code": "JP.4541", "name": "小野薬品工業", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4578", "name": "大塚HD", "sector": "医薬品", "estimated_price": 6000},
    {"code": "JP.4581", "name": "大正製薬HD", "sector": "医薬品", "estimated_price": 8000},
    {"code": "JP.4507", "name": "塩野義製薬", "sector": "医薬品", "estimated_price": 7000},
    # ── 機械 ──
    {"code": "JP.6367", "name": "ダイキン工業", "sector": "機械", "estimated_price": 32000},
    {"code": "JP.6301", "name": "小松製作所", "sector": "機械", "estimated_price": 4000},
    {"code": "JP.6326", "name": "クボタ", "sector": "機械", "estimated_price": 2000},
    {"code": "JP.7011", "name": "三菱重工業", "sector": "機械", "estimated_price": 8000},
    {"code": "JP.7012", "name": "川崎重工業", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.5631", "name": "日本製鉄", "sector": "鉄鋼", "estimated_price": 3500},
    {"code": "JP.5401", "name": "日本製鉄", "sector": "鉄鋼", "estimated_price": 3500},
    {"code": "JP.5406", "name": "神戸製鋼所", "sector": "鉄鋼", "estimated_price": 1800},
    {"code": "JP.5411", "name": "JFEホールディングス", "sector": "鉄鋼", "estimated_price": 2000},
    {"code": "JP.6479", "name": "ミネベアミツミ", "sector": "機械", "estimated_price": 2500},
    {"code": "JP.6481", "name": "THK", "sector": "機械", "estimated_price": 3000},
    {"code": "JP.6592", "name": "マブチモーター", "sector": "機械", "estimated_price": 2500},
    {"code": "JP.6674", "name": "GSユアサ", "sector": "機械", "estimated_price": 3000},
    {"code": "JP.7004", "name": "日立造船", "sector": "機械", "estimated_price": 1000},
    {"code": "JP.7013", "name": "IHI", "sector": "機械", "estimated_price": 4000},
    {"code": "JP.6103", "name": "オークマ", "sector": "機械", "estimated_price": 7000},
    {"code": "JP.6113", "name": "アマダ", "sector": "機械", "estimated_price": 1500},
    # ── 建設 ──
    {"code": "JP.1801", "name": "大成建設", "sector": "建設", "estimated_price": 5000},
    {"code": "JP.1802", "name": "大林組", "sector": "建設", "estimated_price": 2000},
    {"code": "JP.1803", "name": "清水建設", "sector": "建設", "estimated_price": 1200},
    {"code": "JP.1812", "name": "鹿島建設", "sector": "建設", "estimated_price": 3000},
    {"code": "JP.1860", "name": "戸田建設", "sector": "建設", "estimated_price": 1000},
    {"code": "JP.1884", "name": "日本道路", "sector": "建設", "estimated_price": 2500},
    {"code": "JP.1925", "name": "大和ハウス工業", "sector": "建設", "estimated_price": 4000},
    {"code": "JP.1928", "name": "積水ハウス", "sector": "建設", "estimated_price": 3500},
    {"code": "JP.1820", "name": "西松建設", "sector": "建設", "estimated_price": 4000},
    {"code": "JP.1719", "name": "ハザマ", "sector": "建設", "estimated_price": 1200},
    # ── 食品 ──
    {"code": "JP.2501", "name": "サッポロHD", "sector": "食品", "estimated_price": 5000},
    {"code": "JP.2502", "name": "アサヒグループHD", "sector": "食品", "estimated_price": 5500},
    {"code": "JP.2503", "name": "キリンホールディングス", "sector": "食品", "estimated_price": 2200},
    {"code": "JP.2801", "name": "味の素", "sector": "食品", "estimated_price": 6000},
    {"code": "JP.2802", "name": "明治HD", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2875", "name": "東洋水産", "sector": "食品", "estimated_price": 6000},
    {"code": "JP.2897", "name": "日清食品HD", "sector": "食品", "estimated_price": 4500},
    {"code": "JP.2269", "name": "明治HD", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2587", "name": "サントリーB", "sector": "食品", "estimated_price": 4000},
    {"code": "JP.2212", "name": "山崎製パン", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2222", "name": "亀田製菓", "sector": "食品", "estimated_price": 4000},
    {"code": "JP.2282", "name": "日本ハム", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2579", "name": "コカ・コーラB", "sector": "食品", "estimated_price": 6000},
    {"code": "JP.2914", "name": "JT", "sector": "食品", "estimated_price": 4000},
    # ── 小売 ──
    {"code": "JP.9983", "name": "ファーストリテイリング", "sector": "小売", "estimated_price": 45000},
    {"code": "JP.3382", "name": "セブン＆アイHD", "sector": "小売", "estimated_price": 5000},
    {"code": "JP.8267", "name": "イオン", "sector": "小売", "estimated_price": 3000},
    {"code": "JP.9843", "name": "ニトリHD", "sector": "小売", "estimated_price": 35000},
    {"code": "JP.8233", "name": "髙島屋", "sector": "小売", "estimated_price": 3000},
    {"code": "JP.3086", "name": "J.フロントリテイリング", "sector": "小売", "estimated_price": 1500},
    {"code": "JP.3099", "name": "三越伊勢丹HD", "sector": "小売", "estimated_price": 2000},
    {"code": "JP.7453", "name": "良品計画", "sector": "小売", "estimated_price": 2000},
    {"code": "JP.9989", "name": "しまむら", "sector": "小売", "estimated_price": 8000},
    {"code": "JP.8227", "name": "しまむら", "sector": "小売", "estimated_price": 8000},
    {"code": "JP.9719", "name": "SCSK", "sector": "情報・通信", "estimated_price": 3000},
    {"code": "JP.7518", "name": "ネットワンシステムズ", "sector": "小売", "estimated_price": 4000},
    {"code": "JP.9831", "name": "ヤマダHD", "sector": "小売", "estimated_price": 500},
    {"code": "JP.7606", "name": "ユナイテッドアローズ", "sector": "小売", "estimated_price": 3000},
    # ── サービス ──
    {"code": "JP.6098", "name": "リクルートHD", "sector": "サービス", "estimated_price": 10000},
    {"code": "JP.4661", "name": "オリエンタルランド", "sector": "サービス", "estimated_price": 5000},
    {"code": "JP.2121", "name": "ミクシィ", "sector": "サービス", "estimated_price": 3000},
    {"code": "JP.2432", "name": "電通グループ", "sector": "サービス", "estimated_price": 4000},
    {"code": "JP.4324", "name": "電通グループ", "sector": "サービス", "estimated_price": 4000},
    {"code": "JP.9602", "name": "東宝", "sector": "サービス", "estimated_price": 5000},
    {"code": "JP.9603", "name": "東急レクリエーション", "sector": "サービス", "estimated_price": 5000},
    {"code": "JP.9412", "name": "スカパーJSAT", "sector": "サービス", "estimated_price": 1500},
    {"code": "JP.3474", "name": "リログループ", "sector": "サービス", "estimated_price": 3000},
    # ── 不動産 ──
    {"code": "JP.8801", "name": "三菱地所", "sector": "不動産", "estimated_price": 2500},
    {"code": "JP.8802", "name": "三菱地所", "sector": "不動産", "estimated_price": 2500},
    {"code": "JP.8803", "name": "三井不動産", "sector": "不動産", "estimated_price": 1500},
    {"code": "JP.8804", "name": "東京建物", "sector": "不動産", "estimated_price": 1000},
    {"code": "JP.8830", "name": "住友不動産", "sector": "不動産", "estimated_price": 5000},
    {"code": "JP.8860", "name": "フージャースHD", "sector": "不動産", "estimated_price": 800},
    {"code": "JP.8876", "name": "リログループ", "sector": "不動産", "estimated_price": 3000},
    {"code": "JP.8905", "name": "東宝", "sector": "不動産", "estimated_price": 5000},
    {"code": "JP.8934", "name": "サンフロンティア不動産", "sector": "不動産", "estimated_price": 1500},
    # ── 証券 ──
    {"code": "JP.8604", "name": "野村ホールディングス", "sector": "証券", "estimated_price": 900},
    {"code": "JP.8601", "name": "大和証券G本社", "sector": "証券", "estimated_price": 1000},
    {"code": "JP.8628", "name": "松井証券", "sector": "証券", "estimated_price": 1000},
    {"code": "JP.8707", "name": "岩井コスモ証券", "sector": "証券", "estimated_price": 1500},
    {"code": "JP.8708", "name": "いちよし証券", "sector": "証券", "estimated_price": 800},
    # ── 陸運 ──
    {"code": "JP.6178", "name": "日本郵政HD", "sector": "陸運", "estimated_price": 1500},
    {"code": "JP.9005", "name": "東急", "sector": "陸運", "estimated_price": 2000},
    {"code": "JP.9007", "name": "小田急電鉄", "sector": "陸運", "estimated_price": 3000},
    {"code": "JP.9008", "name": "京王電鉄", "sector": "陸運", "estimated_price": 4000},
    {"code": "JP.9009", "name": "京成電鉄", "sector": "陸運", "estimated_price": 4000},
    {"code": "JP.9020", "name": "JR東日本", "sector": "陸運", "estimated_price": 3000},
    {"code": "JP.9021", "name": "JR西日本", "sector": "陸運", "estimated_price": 4000},
    {"code": "JP.9022", "name": "JR東海", "sector": "陸運", "estimated_price": 35000},
    {"code": "JP.9045", "name": "京阪HD", "sector": "陸運", "estimated_price": 3000},
    {"code": "JP.9048", "name": "名古屋鉄道", "sector": "陸運", "estimated_price": 2000},
    {"code": "JP.9062", "name": "日本通運", "sector": "陸運", "estimated_price": 5000},
    {"code": "JP.9064", "name": "ヤマトHD", "sector": "陸運", "estimated_price": 1800},
    {"code": "JP.9101", "name": "日本郵船", "sector": "海運", "estimated_price": 5000},
    {"code": "JP.9104", "name": "商船三井", "sector": "海運", "estimated_price": 5000},
    {"code": "JP.9107", "name": "川崎汽船", "sector": "海運", "estimated_price": 3000},
    # ── 空運 ──
    {"code": "JP.9201", "name": "ANAホールディングス", "sector": "空運", "estimated_price": 4000},
    {"code": "JP.9202", "name": "日本航空", "sector": "空運", "estimated_price": 3000},
    {"code": "JP.9231", "name": "ANAホールディングス", "sector": "空運", "estimated_price": 4000},
    # ── 鉱業 ──
    {"code": "JP.5020", "name": "ENEOSホールディングス", "sector": "鉱業", "estimated_price": 800},
    {"code": "JP.1605", "name": "INPEX", "sector": "鉱業", "estimated_price": 2000},
    {"code": "JP.1662", "name": "石油資源開発", "sector": "鉱業", "estimated_price": 1000},
    # ── 電力 ──
    {"code": "JP.9501", "name": "東京電力HD", "sector": "電力", "estimated_price": 900},
    {"code": "JP.9502", "name": "中部電力", "sector": "電力", "estimated_price": 2000},
    {"code": "JP.9503", "name": "関西電力", "sector": "電力", "estimated_price": 2000},
    {"code": "JP.9504", "name": "中国電力", "sector": "電力", "estimated_price": 1000},
    {"code": "JP.9505", "name": "北陸電力", "sector": "電力", "estimated_price": 1000},
    {"code": "JP.9506", "name": "東北電力", "sector": "電力", "estimated_price": 1000},
    {"code": "JP.9507", "name": "四国電力", "sector": "電力", "estimated_price": 1000},
    {"code": "JP.9508", "name": "九州電力", "sector": "電力", "estimated_price": 1500},
    {"code": "JP.9509", "name": "北海道電力", "sector": "電力", "estimated_price": 800},
    {"code": "JP.9513", "name": "Jパワー", "sector": "電力", "estimated_price": 3000},
    # ── ガス ──
    {"code": "JP.9531", "name": "東京ガス", "sector": "ガス", "estimated_price": 3000},
    {"code": "JP.9532", "name": "大阪ガス", "sector": "ガス", "estimated_price": 3000},
    {"code": "JP.9533", "name": "東邦ガス", "sector": "ガス", "estimated_price": 3000},
    {"code": "JP.9534", "name": "北海道瓦斯", "sector": "ガス", "estimated_price": 3000},
    # ── 保険 ──
    {"code": "JP.8630", "name": "SOMPOホールディングス", "sector": "保険", "estimated_price": 8000},
    {"code": "JP.8725", "name": "MS&ADインシュアランス", "sector": "保険", "estimated_price": 3000},
    {"code": "JP.8766", "name": "東京海上HD", "sector": "保険", "estimated_price": 6000},
    {"code": "JP.8795", "name": "T&Dホールディングス", "sector": "保険", "estimated_price": 2500},
    {"code": "JP.7180", "name": "第一生命HD", "sector": "保険", "estimated_price": 2500},
    {"code": "JP.8729", "name": "ソニーFG", "sector": "保険", "estimated_price": 4000},
    {"code": "JP.8750", "name": "第一生命HD", "sector": "保険", "estimated_price": 2500},
    {"code": "JP.8793", "name": "NKSJホールディングス", "sector": "保険", "estimated_price": 3000},
    # ── その他金融 ──
    {"code": "JP.8253", "name": "クレディセゾン", "sector": "その他金融", "estimated_price": 3000},
    {"code": "JP.8511", "name": "日本証券金融", "sector": "その他金融", "estimated_price": 2000},
    {"code": "JP.8572", "name": "アコム", "sector": "その他金融", "estimated_price": 4000},
    {"code": "JP.8591", "name": "オリックス", "sector": "その他金融", "estimated_price": 3000},
    {"code": "JP.8593", "name": "三菱HCキャピタル", "sector": "その他金融", "estimated_price": 1000},
    # ── 繊維 ──
    {"code": "JP.3101", "name": "東洋紡", "sector": "繊維", "estimated_price": 1500},
    {"code": "JP.3105", "name": "日清紡HD", "sector": "繊維", "estimated_price": 1000},
    {"code": "JP.3401", "name": "帝人", "sector": "繊維", "estimated_price": 1500},
    {"code": "JP.3402", "name": "東レ", "sector": "繊維", "estimated_price": 1000},
    # ── ガラス・窯業 ──
    {"code": "JP.5201", "name": "AGC", "sector": "ガラス・窯業", "estimated_price": 4000},
    {"code": "JP.5202", "name": "日本板硝子", "sector": "ガラス・窯業", "estimated_price": 500},
    {"code": "JP.5214", "name": "日本電気硝子", "sector": "ガラス・窯業", "estimated_price": 3000},
    {"code": "JP.5232", "name": "住友大阪セメント", "sector": "ガラス・窯業", "estimated_price": 3000},
    {"code": "JP.5233", "name": "太平洋セメント", "sector": "ガラス・窯業", "estimated_price": 3000},
    # ── ゴム製品 ──
    {"code": "JP.5101", "name": "横浜ゴム", "sector": "ゴム製品", "estimated_price": 3000},
    {"code": "JP.5105", "name": "TOYO TIRE", "sector": "ゴム製品", "estimated_price": 2500},
    {"code": "JP.5110", "name": "住友ゴム工業", "sector": "ゴム製品", "estimated_price": 1500},
    # ── パルプ・紙 ──
    {"code": "JP.3861", "name": "王子ホールディングス", "sector": "パルプ・紙", "estimated_price": 600},
    {"code": "JP.3863", "name": "日本製紙", "sector": "パルプ・紙", "estimated_price": 1500},
    {"code": "JP.3865", "name": "北越コーポレーション", "sector": "パルプ・紙", "estimated_price": 1000},
    # ── 倉庫 ──
    {"code": "JP.9301", "name": "三菱倉庫", "sector": "倉庫", "estimated_price": 3000},
    {"code": "JP.9302", "name": "三井倉庫HD", "sector": "倉庫", "estimated_price": 3000},
    {"code": "JP.9310", "name": "日本トランスシティ", "sector": "倉庫", "estimated_price": 1500},
    # ── ETF ──
    {"code": "JP.1306", "name": "TOPIX連動ETF", "sector": "ETF", "estimated_price": 2500},
    {"code": "JP.1320", "name": "iFreeETF日経225", "sector": "ETF", "estimated_price": 4000},
    {"code": "JP.2559", "name": "MAXIS全世界株式(オルカン)", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.2558", "name": "MAXIS米国株式(S&P500)", "sector": "ETF", "estimated_price": 2500},
    {"code": "JP.2563", "name": "iFreeNEXT TOPIX連動", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.1570", "name": "NEXT NOTES 日経平均", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.1365", "name": "iFreeETF TOPIX", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.2513", "name": "MAXIS TOPIX", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.2568", "name": "MAXIS NASDAQ100", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.2621", "name": "iFreeETF NASDAQ100", "sector": "ETF", "estimated_price": 2000},
    {"code": "JP.2630", "name": "MAXIS 豪州リート", "sector": "ETF", "estimated_price": 2000},
    # ── 追加: 電気機器 ──
    {"code": "JP.6504", "name": "富士電機", "sector": "電気機器", "estimated_price": 6000},
    {"code": "JP.6506", "name": "安川電機", "sector": "電気機器", "estimated_price": 6000},
    {"code": "JP.6641", "name": "日新電機", "sector": "電気機器", "estimated_price": 4000},
    {"code": "JP.6703", "name": "OKI", "sector": "電気機器", "estimated_price": 2000},
    {"code": "JP.6724", "name": "セイコーエプソン", "sector": "電気機器", "estimated_price": 2500},
    {"code": "JP.6750", "name": "エレコム", "sector": "電気機器", "estimated_price": 1500},
    {"code": "JP.6806", "name": "ヒロセ電機", "sector": "電気機器", "estimated_price": 15000},
    {"code": "JP.6849", "name": "日本光電", "sector": "電気機器", "estimated_price": 5000},
    {"code": "JP.6856", "name": "堀場製作所", "sector": "電気機器", "estimated_price": 12000},
    {"code": "JP.6869", "name": "シスメックス", "sector": "電気機器", "estimated_price": 8000},
    {"code": "JP.6871", "name": "日本マイクロニクス", "sector": "電気機器", "estimated_price": 6000},
    # ── 追加: 化学 ──
    {"code": "JP.4182", "name": "三菱瓦斯化学", "sector": "化学", "estimated_price": 3000},
    {"code": "JP.4185", "name": "JSR", "sector": "化学", "estimated_price": 4000},
    {"code": "JP.4186", "name": "東京応化工業", "sector": "化学", "estimated_price": 5000},
    {"code": "JP.4202", "name": "ダイセル", "sector": "化学", "estimated_price": 1500},
    {"code": "JP.4208", "name": "UACJ", "sector": "化学", "estimated_price": 3000},
    {"code": "JP.4212", "name": "積水化学工業", "sector": "化学", "estimated_price": 2500},
    {"code": "JP.4228", "name": "積水化成品工業", "sector": "化学", "estimated_price": 1000},
    {"code": "JP.4403", "name": "日油", "sector": "化学", "estimated_price": 5000},
    {"code": "JP.4461", "name": "第一工業製薬", "sector": "化学", "estimated_price": 3000},
    {"code": "JP.4626", "name": "太陽HD", "sector": "化学", "estimated_price": 4000},
    # ── 追加: 医薬品 ──
    {"code": "JP.4527", "name": "ロート製薬", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4528", "name": "小野薬品工業", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4540", "name": "ツムラ", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4551", "name": "鳥居薬品", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4552", "name": "杏林製薬", "sector": "医薬品", "estimated_price": 2000},
    {"code": "JP.4553", "name": "東和薬品", "sector": "医薬品", "estimated_price": 3000},
    {"code": "JP.4574", "name": "あすか製薬HD", "sector": "医薬品", "estimated_price": 1500},
    {"code": "JP.4587", "name": "ペプチドリーム", "sector": "医薬品", "estimated_price": 3000},
    # ── 追加: 建設 ──
    {"code": "JP.1813", "name": "不動テトラ", "sector": "建設", "estimated_price": 3000},
    {"code": "JP.1815", "name": "前田建設工業", "sector": "建設", "estimated_price": 1500},
    {"code": "JP.1878", "name": "大東建託", "sector": "建設", "estimated_price": 15000},
    {"code": "JP.1885", "name": "東亜建設工業", "sector": "建設", "estimated_price": 1000},
    {"code": "JP.1890", "name": "東洋建設", "sector": "建設", "estimated_price": 1000},
    {"code": "JP.1921", "name": "タマホーム", "sector": "建設", "estimated_price": 3000},
    {"code": "JP.8929", "name": "青山財産ネットワークス", "sector": "建設", "estimated_price": 1000},
    # ── 追加: 食品 ──
    {"code": "JP.2267", "name": "ヤクルト本社", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2281", "name": "プリマハム", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2809", "name": "キユーピー", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2810", "name": "ハウス食品G", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2811", "name": "カゴメ", "sector": "食品", "estimated_price": 3000},
    {"code": "JP.2871", "name": "ニチレイ", "sector": "食品", "estimated_price": 4000},
    {"code": "JP.2874", "name": "ニッスイ", "sector": "食品", "estimated_price": 1500},
    {"code": "JP.2915", "name": "ケンコーマヨネーズ", "sector": "食品", "estimated_price": 2000},
    # ── 追加: 小売 ──
    {"code": "JP.2651", "name": "ローソン", "sector": "小売", "estimated_price": 3000},
    {"code": "JP.2670", "name": "ABCマート", "sector": "小売", "estimated_price": 6000},
    {"code": "JP.8255", "name": "丸悦", "sector": "小売", "estimated_price": 1000},
    {"code": "JP.8260", "name": "井筒屋", "sector": "小売", "estimated_price": 1000},
    {"code": "JP.8270", "name": "ヤマダHD", "sector": "小売", "estimated_price": 500},
    {"code": "JP.8282", "name": "ケーズHD", "sector": "小売", "estimated_price": 1500},
    {"code": "JP.9830", "name": "トリドールHD", "sector": "小売", "estimated_price": 3000},
    {"code": "JP.9842", "name": "ツルハHD", "sector": "小売", "estimated_price": 8000},
    # ── 追加: サービス ──
    {"code": "JP.2130", "name": "メンバーズ", "sector": "サービス", "estimated_price": 1000},
    {"code": "JP.2157", "name": "コシダカHD", "sector": "サービス", "estimated_price": 1500},
    {"code": "JP.2371", "name": "カカクコム", "sector": "サービス", "estimated_price": 2000},
    {"code": "JP.2395", "name": "新日本科学", "sector": "サービス", "estimated_price": 1500},
    {"code": "JP.2413", "name": "エムスリー", "sector": "サービス", "estimated_price": 2000},
    {"code": "JP.2433", "name": "博報堂DYHD", "sector": "サービス", "estimated_price": 1500},
    {"code": "JP.9601", "name": "松竹", "sector": "サービス", "estimated_price": 9000},
    {"code": "JP.9715", "name": "トランス・コスモス", "sector": "サービス", "estimated_price": 3000},
    {"code": "JP.9744", "name": "メイテック", "sector": "サービス", "estimated_price": 5000},
    {"code": "JP.9759", "name": "NSD", "sector": "サービス", "estimated_price": 3000},
    # ── 追加: 不動産 ──
    {"code": "JP.3222", "name": "ユナイテッド・アーバン", "sector": "不動産", "estimated_price": 1000},
    {"code": "JP.3231", "name": "野村不動産HD", "sector": "不動産", "estimated_price": 4000},
    {"code": "JP.3249", "name": "産業ファンド", "sector": "不動産", "estimated_price": 1000},
    {"code": "JP.3250", "name": "ADワークスG", "sector": "不動産", "estimated_price": 1000},
    {"code": "JP.8877", "name": "日本エスコン", "sector": "不動産", "estimated_price": 1000},
    {"code": "JP.8897", "name": "ミサワホーム", "sector": "不動産", "estimated_price": 1500},
    # ── 追加: 機械 ──
    {"code": "JP.6273", "name": "SMC", "sector": "機械", "estimated_price": 6000},
    {"code": "JP.6323", "name": "ローランドDG", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.6351", "name": "鶴見製作所", "sector": "機械", "estimated_price": 3000},
    {"code": "JP.6355", "name": "三菱化工機", "sector": "機械", "estimated_price": 3000},
    {"code": "JP.6361", "name": "荏原製作所", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.6368", "name": "オルガノ", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.6395", "name": "マキタ", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.6457", "name": "グローリー", "sector": "機械", "estimated_price": 3000},
    {"code": "JP.6471", "name": "日本精工", "sector": "機械", "estimated_price": 1500},
    {"code": "JP.6472", "name": "NTN", "sector": "機械", "estimated_price": 500},
    {"code": "JP.6586", "name": "マキタ", "sector": "機械", "estimated_price": 5000},
    {"code": "JP.6588", "name": "東芝テック", "sector": "機械", "estimated_price": 3000},
    # ── 追加: 銀行 ──
    {"code": "JP.8334", "name": "群馬銀行", "sector": "銀行", "estimated_price": 1000},
    {"code": "JP.8336", "name": "武蔵野銀行", "sector": "銀行", "estimated_price": 2000},
    {"code": "JP.8341", "name": "栃木銀行", "sector": "銀行", "estimated_price": 500},
    {"code": "JP.8358", "name": "スルガ銀行", "sector": "銀行", "estimated_price": 1000},
    {"code": "JP.8366", "name": "滋賀銀行", "sector": "銀行", "estimated_price": 3000},
    {"code": "JP.8370", "name": "紀陽銀行", "sector": "銀行", "estimated_price": 2000},
    # ── 追加: 証券 ──
    {"code": "JP.8616", "name": "東海東京FH", "sector": "証券", "estimated_price": 1000},
    {"code": "JP.8617", "name": "光世証券", "sector": "証券", "estimated_price": 500},
    {"code": "JP.8704", "name": "極東証券", "sector": "証券", "estimated_price": 1500},
    # ── 追加: 保険 ──
    {"code": "JP.7164", "name": "全国保証", "sector": "保険", "estimated_price": 5000},
    {"code": "JP.8595", "name": "ジャフコG", "sector": "保険", "estimated_price": 3000},
    {"code": "JP.8714", "name": "池田泉州HD", "sector": "保険", "estimated_price": 500},
    # ── 追加: 陸運・海運 ──
    {"code": "JP.9025", "name": "西武HD", "sector": "陸運", "estimated_price": 2000},
    {"code": "JP.9031", "name": "西日本鉄道", "sector": "陸運", "estimated_price": 3000},
    {"code": "JP.9065", "name": "山九", "sector": "陸運", "estimated_price": 5000},
    {"code": "JP.9104", "name": "商船三井", "sector": "海運", "estimated_price": 5000},
    {"code": "JP.9110", "name": "NSユナイテッド海運", "sector": "海運", "estimated_price": 5000},
    # ── 追加: 空運 ──
    {"code": "JP.9366", "name": "日本航空", "sector": "空運", "estimated_price": 3000},
    # ── 追加: 電力・ガス ──
    {"code": "JP.9509", "name": "北海道電力", "sector": "電力", "estimated_price": 800},
    {"code": "JP.9511", "name": "沖縄電力", "sector": "電力", "estimated_price": 1500},
    {"code": "JP.9543", "name": "東京瓦斯", "sector": "ガス", "estimated_price": 3000},
    # ── 追加: その他 ──
    {"code": "JP.2752", "name": "フジ・メディアHD", "sector": "サービス", "estimated_price": 2000},
    {"code": "JP.4676", "name": "フジHD", "sector": "サービス", "estimated_price": 2000},
    {"code": "JP.4832", "name": "JFEシステムズ", "sector": "情報・通信", "estimated_price": 3000},
    {"code": "JP.7951", "name": "ヤマハ", "sector": "電気機器", "estimated_price": 3000},
    {"code": "JP.7956", "name": "ピジョン", "sector": "化学", "estimated_price": 3000},
    {"code": "JP.8113", "name": "ユニ・チャーム", "sector": "化学", "estimated_price": 5000},
    {"code": "JP.9766", "name": "コナミG", "sector": "サービス", "estimated_price": 8000},
    {"code": "JP.9948", "name": "OKUWA", "sector": "小売", "estimated_price": 5000},
]

# 重複コード除去（定義上重複があれば先勝ち）
_SEEN: set[str] = set()
DEDUPED_CANDIDATES: list[dict[str, Any]] = []
for c in DEFAULT_CANDIDATES:
    if c["code"] not in _SEEN:
        _SEEN.add(c["code"])
        DEDUPED_CANDIDATES.append(c)
DEFAULT_CANDIDATES_CLEAN = DEDUPED_CANDIDATES


def classify_stock(symbol: dict, max_trade_price: int = DEFAULT_MAX_TRADE_PRICE) -> str:
    """銘柄の推定価格に基づいてroleを判定する"""
    price = symbol.get("estimated_price")
    if price is None:
        return ROLE_TRADE_CANDIDATE
    if price > max_trade_price:
        return ROLE_WATCH_ONLY
    if price < MIN_TRADE_PRICE:
        return ROLE_EXCLUDED
    return ROLE_TRADE_CANDIDATE


def is_etf(symbol: dict) -> bool:
    return symbol.get("type") == TYPE_ETF or symbol.get("sector") == "ETF"


def assign_default_role(symbol: dict) -> str:
    """銘柄にデフォルトroleを割り当てる"""
    if is_etf(symbol):
        # ETFは全てbenchmark（売買候補にしない）
        return ROLE_BENCHMARK
    return classify_stock(symbol)


def assign_default_tradable(symbol: dict, max_trade_price: int = DEFAULT_MAX_TRADE_PRICE) -> bool:
    """デフォルトのtradable値を判定"""
    role = symbol.get("role", "")
    if role == ROLE_BENCHMARK:
        return False
    if role == ROLE_WATCH_ONLY:
        return False
    price = symbol.get("estimated_price")
    if price and price > max_trade_price:
        return False
    return True


def build_symbol_entry(code: str, name: str, sector: str,
                       estimated_price: Optional[float] = None,
                       existing: Optional[dict] = None,
                       max_trade_price: int = DEFAULT_MAX_TRADE_PRICE) -> dict:
    """銘柄エントリを構築する。existingがあればrole/tradable/notesを維持"""
    if existing:
        return {
            "code": code,
            "name": name,
            "type": existing.get("type", TYPE_STOCK),
            "role": existing.get("role", ROLE_TRADE_CANDIDATE),
            "tradable": existing.get("tradable", True),
            "sector": sector,
            "benchmark_group": existing.get("benchmark_group"),
            "notes": existing.get("notes", ""),
        }

    entry = {
        "code": code,
        "name": name,
        "type": TYPE_STOCK,
        "role": ROLE_TRADE_CANDIDATE,
        "tradable": True,
        "sector": sector,
        "benchmark_group": None,
        "notes": "",
    }

    # ETF判定
    if sector == "ETF":
        entry["type"] = TYPE_ETF

    # role判定
    if sector == "ETF":
        entry["role"] = ROLE_BENCHMARK
        entry["tradable"] = False
        entry["notes"] = f"ETF。{ROLE_BENCHMARK}専用"
    elif estimated_price is not None:
        if estimated_price > max_trade_price:
            entry["role"] = ROLE_WATCH_ONLY
            entry["tradable"] = False
            entry["notes"] = f"高額株(約{estimated_price}円)。監視専用"
        elif estimated_price < MIN_TRADE_PRICE:
            entry["role"] = ROLE_EXCLUDED
            entry["tradable"] = False
            entry["notes"] = f"低位株(約{estimated_price}円)。取引対象外"
        else:
            entry["role"] = ROLE_TRADE_CANDIDATE
            entry["tradable"] = True

    return entry


class UniverseBuilder:
    """ユニバース構築クラス"""

    def __init__(self, existing_path: Optional[str] = None, config_path: str = "config.yaml",
                 max_trade_price: int = DEFAULT_MAX_TRADE_PRICE):
        self.existing_path = existing_path
        self.config_path = config_path
        self.max_trade_price = max_trade_price

    def load_existing(self, path: Optional[str] = None) -> dict[str, dict]:
        """既存のsymbols.jsonを読み込む"""
        load_path = path or self.existing_path
        if not load_path or not os.path.exists(load_path):
            return {}
        with open(load_path, encoding="utf-8") as f:
            symbols = json.load(f)
        return {s["code"]: s for s in symbols}

    def generate_candidates(self, top_n: int = 300) -> list[dict]:
        """デフォルト候補リストから指定件数を生成"""
        candidates = []
        for candidate in DEFAULT_CANDIDATES_CLEAN[:top_n]:
            normalized = dict(candidate)
            if normalized.get("sector") == "ETF":
                normalized["type"] = TYPE_ETF
            else:
                normalized.setdefault("type", TYPE_STOCK)
            candidates.append(normalized)
        return candidates

    def merge(self, existing: dict[str, dict], candidates: list[dict]) -> list[dict]:
        """既存symbols.jsonと新規候補をマージする（既存優先）"""
        merged = {}
        used = set(existing.keys())

        # 既存のrole/tradable/notesをそのまま維持
        for code, symbol in existing.items():
            merged[code] = dict(symbol)
            # 完全なフィールドを確保
            if "sector" not in merged[code]:
                merged[code]["sector"] = ""
            if "type" not in merged[code]:
                merged[code]["type"] = TYPE_STOCK
            if "benchmark_group" not in merged[code]:
                merged[code]["benchmark_group"] = None
            if "notes" not in merged[code]:
                merged[code]["notes"] = ""

        # 新規追加（既存にないコードのみ）
        for c in candidates:
            code = c["code"]
            if code in used:
                continue
            entry = build_symbol_entry(
                code=code,
                name=c["name"],
                sector=c["sector"],
                estimated_price=c.get("estimated_price"),
                max_trade_price=self.max_trade_price,
            )
            merged[code] = entry

        # code順にソート
        result = sorted(merged.values(), key=lambda x: x["code"])
        return result

    def save(self, symbols: list[dict], output_path: str) -> str:
        """symbols.jsonに保存"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(symbols, f, ensure_ascii=False, indent=2)
        logger.info("保存完了: %s (%d銘柄)", output_path, len(symbols))
        return output_path

    def export_summary_csv(self, symbols: list[dict], output_dir: str) -> str:
        """サマリーCSVを出力"""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        mtp = self.max_trade_price
        high_label = f">{mtp}"

        # role別
        role_counts: dict[str, int] = {}
        # tradable別
        tradable_counts = {"true": 0, "false": 0}
        # sector別
        sector_counts: dict[str, int] = {}
        # type別
        type_counts: dict[str, int] = {}
        # price range別（estimated_priceがない場合はunknown）
        price_ranges = {"<500": 0, "500-3000": 0, "3000-10000": 0, f"10000-{mtp}": 0, high_label: 0, "unknown": 0}

        for s in symbols:
            role = s.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

            tradable = s.get("tradable", True)
            if tradable:
                tradable_counts["true"] += 1
            else:
                tradable_counts["false"] += 1

            sector = s.get("sector", "unknown")
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

            type_ = s.get("type", "unknown")
            type_counts[type_] = type_counts.get(type_, 0) + 1

            price = s.get("estimated_price")
            if price is None:
                price_ranges["unknown"] += 1
            elif price < 500:
                price_ranges["<500"] += 1
            elif price < 3000:
                price_ranges["500-3000"] += 1
            elif price < 10000:
                price_ranges["3000-10000"] += 1
            elif price <= mtp:
                price_ranges[f"10000-{mtp}"] += 1
            else:
                price_ranges[high_label] += 1

        trade_candidate_count = role_counts.get(ROLE_TRADE_CANDIDATE, 0)
        watch_only_count = role_counts.get(ROLE_WATCH_ONLY, 0)
        benchmark_count = role_counts.get(ROLE_BENCHMARK, 0)
        excluded_count = role_counts.get(ROLE_EXCLUDED, 0)

        # CSV書き込み
        date_str = datetime.now().strftime("%Y%m%d")
        csv_path = Path(output_dir) / f"universe_build_summary_{date_str}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            writer.writerow(["total_symbols", len(symbols)])
            writer.writerow([f"role:{ROLE_TRADE_CANDIDATE}", trade_candidate_count])
            writer.writerow([f"role:{ROLE_WATCH_ONLY}", watch_only_count])
            writer.writerow([f"role:{ROLE_BENCHMARK}", benchmark_count])
            writer.writerow([f"role:{ROLE_EXCLUDED}", excluded_count])
            writer.writerow(["tradable:true", tradable_counts["true"]])
            writer.writerow(["tradable:false", tradable_counts["false"]])
            for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
                writer.writerow([f"sector:{sector}", count])
            for pr, count in sorted(price_ranges.items()):
                writer.writerow([f"price_range:{pr}", count])
            writer.writerow(["type:stock", type_counts.get(TYPE_STOCK, 0)])
            writer.writerow(["type:etf", type_counts.get(TYPE_ETF, 0)])

        logger.info("サマリーCSV出力: %s", csv_path)
        return str(csv_path)

    def print_diagnostics(self, symbols: list[dict]) -> None:
        """診断情報をコンソールに表示"""
        if not symbols:
            print("銘柄がありません")
            return
        mtp = self.max_trade_price

        role_counts: dict[str, int] = {}
        sector_counts: dict[str, int] = {}
        tradable_true = 0
        price_high = 0

        for s in symbols:
            role = s.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

            sector = s.get("sector", "unknown")
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

            if s.get("tradable"):
                tradable_true += 1

            price = s.get("estimated_price")
            if price and price > mtp:
                price_high += 1

        trade_candidate_count = role_counts.get(ROLE_TRADE_CANDIDATE, 0)
        watch_only_count = role_counts.get(ROLE_WATCH_ONLY, 0)
        benchmark_count = role_counts.get(ROLE_BENCHMARK, 0)
        excluded_count = role_counts.get(ROLE_EXCLUDED, 0)

        print(f"\n全銘柄数: {len(symbols)}")
        print("role別:")
        print(f"  {ROLE_TRADE_CANDIDATE}: {trade_candidate_count}")
        print(f"  {ROLE_WATCH_ONLY}: {watch_only_count}")
        print(f"  {ROLE_BENCHMARK}: {benchmark_count}")
        print(f"  {ROLE_EXCLUDED}: {excluded_count}")
        print(f"tradable=true: {tradable_true}")
        print(f"price > {mtp}: {price_high}")
        print("\nsector別件数:")
        for sector, cnt in sorted(sector_counts.items(), key=lambda x: -x[1]):
            print(f"  {sector}: {cnt}件")

        # 警告
        warnings = []
        if trade_candidate_count < 50:
            warnings.append(f"{ROLE_TRADE_CANDIDATE}が{trade_candidate_count}件と少なすぎます")
        if len(sector_counts) < 8:
            warnings.append(f"セクター数が{len(sector_counts)}と少なすぎます")
        top_sector_cnt = max(list(sector_counts.values()))
        top_sector_pct = top_sector_cnt / len(symbols) * 100
        if top_sector_pct > 30:
            dominant = max(sector_counts.items(), key=lambda x: x[1])[0]
            warnings.append(f"セクター偏り: {dominant}が{top_sector_pct:.0f}%を占めています")

        if warnings:
            print("\n警告:")
            for w in warnings:
                print(f"  [WARN] {w}")

        trade_candidate_count = role_counts.get(ROLE_TRADE_CANDIDATE, 0)
        watch_only_count = role_counts.get(ROLE_WATCH_ONLY, 0)
        benchmark_count = role_counts.get(ROLE_BENCHMARK, 0)

        print("\n目的の合格ライン:")
        print(f"  全銘柄: {len(symbols)} (目標: 200〜500)")
        print(f"  trade_candidate: {trade_candidate_count} (目標: 150以上)")
        print(f"  watch_only: {watch_only_count} (目標: 30〜100)")
        print(f"  benchmark/ETF: {benchmark_count} (目標: 10〜30)")

    def build(self, top_n: int = 300,
              output_path: str = "data/symbols.generated.json",
              existing_path: Optional[str] = None,
              merge: bool = True,
              update_config: bool = False,
              max_trade_price: Optional[int] = None) -> list[dict]:
        """
        ユニバースを構築する

        Args:
            top_n: 上位N銘柄
            output_path: 出力先
            existing_path: 既存symbols.jsonのパス（Noneの場合はself.existing_path）
            merge: 既存symbols.jsonとマージするか
            update_config: config.yamlのsymbols_fileを更新するか
            max_trade_price: 買い可能価格上限（Noneでself.max_trade_price）

        Returns:
            構築されたsymbolsリスト
        """
        if max_trade_price is not None:
            self.max_trade_price = max_trade_price

        existing = {}
        if merge:
            load_path = existing_path or self.existing_path
            if load_path:
                existing = self.load_existing(load_path)

        candidates = self.generate_candidates(top_n)
        symbols = self.merge(existing, candidates)

        self.save(symbols, output_path)
        self.print_diagnostics(symbols)
        self.export_summary_csv(symbols, "reports")

        # config更新
        if update_config:
            self._update_config_symbols_file(output_path)

        return symbols

    def _update_config_symbols_file(self, path: str) -> None:
        """config.yamlを更新してsymbols_fileを変更する"""
        import yaml
        config_path = Path(self.config_path)
        if not config_path.exists():
            logger.warning("config.yamlが見つかりません: %s", config_path)
            return
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if "watchlist" not in config:
            config["watchlist"] = {}
        config["watchlist"]["symbols_file"] = path
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        logger.info("config.yaml更新: watchlist.symbols_file = %s", path)
