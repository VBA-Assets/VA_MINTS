"""個人情報パターンの検出（要件定義書 4.9「個人情報パターンの検出」）。

PDFのテキストレイヤー及びファイル名から、正規表現により個人情報らしき
パターン（マイナンバー・電話番号・メールアドレス・郵便番号＋住所・銀行口座
番号・クレジットカード番号）を検出する。検出のみを行い、削除・墨消しの
実行はしない（チェック専念方針）。誤検出（事件番号・書証中の日付等を誤って
拾う場合）があり得るため、呼び出し側（ui/formatting.py）は断定を避けた
警告文言を用いること。

処理はローカルの正規表現マッチング（と、クレジットカード番号カテゴリの
Luhnチェック）のみで行い、外部サービスへの送信は一切行わない
（CLAUDE.md「完全ローカル動作」原則）。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pdfplumber

from mints_prep.config import PiiDetectionConfig
from mints_prep.models import PiiFinding

_CREDIT_CARD_CATEGORY = "クレジットカード番号"

# NFKC正規化では半角ハイフンに揃わないダッシュ類（ハイフン・ダーシ・マイナス
# 記号・長音記号）。日本語PDFのフォントやIMEにより電話番号等の区切りとして
# 実際に現れるため、検出用の正規化で「-」へ写像する。
_DASH_VARIANTS = str.maketrans({c: "-" for c in "‐‑‒–—―−ー"})


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _detect_categories(text: str, patterns: dict[str, str]) -> set[str]:
    # 全角数字・全角ハイフン等の表記（０９０－１２３４－…）も検出できるよう、
    # NFKC正規化（全角→半角）とダッシュ類の写像を施してからマッチする。
    # 正規化は検出用の一時的な変換であり、元のファイル・テキストは一切変更しない。
    normalized = unicodedata.normalize("NFKC", text).translate(_DASH_VARIANTS)
    categories: set[str] = set()
    for category, pattern in patterns.items():
        for m in re.finditer(pattern, normalized):
            if category == _CREDIT_CARD_CATEGORY:
                digits = re.sub(r"[ -]", "", m.group())
                if not _luhn_valid(digits):
                    continue
            categories.add(category)
    return categories


def _finding_from_categories(categories: set[str], detail_prefix: str) -> PiiFinding:
    if not categories:
        return PiiFinding(has_pii=False)
    ordered = tuple(sorted(categories))
    details = tuple(f"{detail_prefix}に{c}の可能性がある記載を検出" for c in ordered)
    return PiiFinding(has_pii=True, detected_categories=ordered, details=details)


def detect_pii_in_filename(filename: str, config: PiiDetectionConfig) -> PiiFinding:
    """ファイル名から個人情報らしきパターンを検出する（全形式共通）。"""
    categories = _detect_categories(filename, config.patterns)
    return _finding_from_categories(categories, "ファイル名")


def detect_pii_in_pdf_text(path: Path, config: PiiDetectionConfig) -> PiiFinding | None:
    """PDFの全ページのテキストレイヤーから個人情報らしきパターンを検出する。

    解析自体に失敗した場合（破損PDF等）はNoneを返す。「検出なし
    （has_pii=False）」と「検出を実行できなかった（None）」を区別しないと、
    解析失敗が無警告の『個人情報なし』に化けてしまうため（墨消し検証
    〔redaction_detector〕がNoneを返してwalker側で注記する方式と揃える）。
    """
    categories_by_page: dict[str, set[int]] = {}
    try:
        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for category in _detect_categories(text, config.patterns):
                    categories_by_page.setdefault(category, set()).add(index)
    except Exception:
        return None

    if not categories_by_page:
        return PiiFinding(has_pii=False)

    ordered = tuple(sorted(categories_by_page))
    details = tuple(
        f"PDF本文に{category}の可能性がある記載を検出"
        f"（{'、'.join(str(p) for p in sorted(categories_by_page[category]))}ページ目）"
        for category in ordered
    )
    return PiiFinding(has_pii=True, detected_categories=ordered, details=details)


def merge_pii_findings(*findings: PiiFinding) -> PiiFinding:
    categories: set[str] = set()
    details: list[str] = []
    for finding in findings:
        categories |= set(finding.detected_categories)
        details.extend(finding.details)
    if not categories:
        return PiiFinding(has_pii=False)
    return PiiFinding(has_pii=True, detected_categories=tuple(sorted(categories)), details=tuple(details))
