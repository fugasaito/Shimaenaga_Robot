# ==============================================================
#  シマエナガロボット メインプログラム
#  マイコン : Freenove ESP32 WROOM-32E
#  言語     : MicroPython v1.28.0
# ==============================================================
#
# 【このプログラムでできること】
#   ・頭を撫でる（タッチセンサー）→ 喜んで鳴く・羽をパタパタ
#   ・つまみを回す（可変抵抗）    → モードを選ぶ
#   ・3秒間頭を押し続ける         → 選んだモードを決定
#   ・タイマー機能（1分・3分・5分）
#   ・10秒ぴったり当てるゲーム
#   ・目の色・音量をカスタマイズして保存
#   ・WiFi でスマホから操作・設定
#     （起動時につまみを左端にするとオフ）
#
# 【プログラムの流れ】
#   1. 定数・設定の読み込み
#   2. ハードウェア（サーボ・LED・ブザー）の関数定義
#   3. 入力読み取り・表示の関数定義
#   4. 機能（タイマー・ゲーム・リアクション）の定義
#   5. バックグラウンドタスクの定義
#   6. WiFi・Webサーバーの定義
#   7. main() で全部を起動
# ==============================================================


# ==============================================================
# 【1-1】 ライブラリの読み込み
# ==============================================================
# asyncio : 複数の処理（LED・音・サーボ）を同時に動かすための仕組み
# machine : ESP32 のピン（GPIO）を操作するライブラリ
# time    : 時間の計測に使う
# random  : ランダムな動作（気まぐれな羽ばたき）に使う
# json    : 設定をファイルに保存・読み込みするための形式
# network : WiFi の設定に使う
import asyncio
from machine import Pin, PWM, ADC
import time
import random
import json
import network


# ==============================================================
# 【1-2】 ユーザー設定 ← ここを変えてカスタマイズ！
# ==============================================================

# 通常時の目の色（R=赤, G=緑, B=青 それぞれ 0〜255）
# 例: (255, 0, 0)=赤  (0, 0, 255)=青  (0, 30, 60)=薄い青（デフォルト）
NORMAL_COLOR = (0, 30, 60)

# 音量（設定モードから変更して保存できる）
# 使える値: 0=無音  33=大  67=中  100=小
# ※パッシブブザーは duty 50% が最大音量なので「小」の値 100 が一番大きく聞こえる
VOLUME = 67

# WiFi アクセスポイントの名前とパスワード
AP_SSID     = "Shimaenaga"   # スマホ側に表示される WiFi 名
AP_PASSWORD = "12345678"     # パスワード（8文字以上）


# ==============================================================
# 【1-3】 定数（プログラム中で変わらない値のまとめ）
# ==============================================================

# --- モード ---
# つまみを回すとホーム画面のモードが変わる
#   左端(0〜25%)  → 設定
#   中左(25〜50%) → タイマー
#   中右(50〜75%) → 通常
#   右端(75〜100%)→ 十秒あてゲーム
MODE_NORMAL   = 0
MODE_TIMER    = 1
MODE_GAME_10S = 2
MODE_COLOR    = 3   # 「設定」モード
MODE_NAMES    = ["通常", "タイマー", "十秒あてゲーム", "設定"]

# --- 音量の段階 ---
# duty_u16 の値 32768 = 50% が最大音量（パッシブブザーの特性）
# VOLUME の値 → デューティ比 = VOLUME / 100 × 32768
VOLUME_LEVELS = [0, 100, 67, 33]           # 無音・小(最大)・中・大(最小)
VOLUME_NAMES  = ["無音", "小", "中", "大"]

# --- タイマーの設定 ---
TIMER_HOME   = -1   # 「ホームに戻る」を表す特別な値
TIMER_LABELS = {TIMER_HOME: "ホームに戻る", 60: "1分", 180: "3分", 300: "5分"}
TIMER_ORDER  = [TIMER_HOME, 60, 180, 300]

# タイマー秒数 → LED の色（1分=緑・3分=黄・5分=赤）
TIMER_COLORS = {
     60: (  0, 200,  50),
    180: (255, 180,   0),
    300: (255,  50,   0),
}

# --- 設定サブメニュー ---
SETTINGS_HOME   = "s_home"
SETTINGS_COLOR  = "s_color"
SETTINGS_VOL    = "s_vol"
SETTINGS_ORDER  = [SETTINGS_HOME, SETTINGS_COLOR, SETTINGS_VOL]
SETTINGS_LABELS = {
    SETTINGS_HOME:  "ホームに戻る",
    SETTINGS_COLOR: "色設定",
    SETTINGS_VOL:   "音量設定",
}

# 設定ファイルのパス（ESP32 のフラッシュメモリに保存される）
SETTINGS_FILE = "shimaenaga_settings.json"


# ==============================================================
# 【1-4】 ログ（動作記録）の仕組み
# ==============================================================
# print() の代わりに log() を使う。
# シリアルモニタと WiFi のログページ両方に出力される。

_log_lines = []   # ログを最大100行まで蓄える入れ物

def log(*args):
    msg = ' '.join(str(a) for a in args)
    print(msg)
    _log_lines.append(msg)
    if len(_log_lines) > 100:
        _log_lines.pop(0)   # 古いものを削除して100行を超えないようにする


# ==============================================================
# 【1-5】 GPIO（ピン）の設定
# ==============================================================
# PWM = パルス幅変調。0〜65535 の数値で明るさや角度を細かく調整できる。
# ADC = アナログ→デジタル変換。可変抵抗の「つまみ量」を数値で読む。

# サーボ（左翼）: GPIO16・freq=50 は「1秒間に50回信号を送る」
servo_l = PWM(Pin(16))
servo_l.freq(50)

# LED（目）: 左目・右目それぞれ R・G・B の3色で制御
# freq=1000 は「1秒間に1000回点滅（速すぎて人間の目には見えない）」
L_R = PWM(Pin(18)); L_G = PWM(Pin(19)); L_B = PWM(Pin(21))  # 左目
R_R = PWM(Pin(22)); R_G = PWM(Pin(23)); R_B = PWM(Pin(25))  # 右目
for ch in (L_R, L_G, L_B, R_R, R_G, R_B):
    ch.freq(1000)

# タッチセンサー: GPIO4。触れると 1、離れると 0
touch = Pin(4, Pin.IN)

# 可変抵抗（つまみ）: GPIO32。0〜65535 で現在の角度を読む
pot = ADC(Pin(32))

# ブザー: GPIO26。最初は音を出さないよう duty_u16(0) にしておく
buzzer = PWM(Pin(26))
buzzer.duty_u16(0)


# ==============================================================
# 【1-6】 状態変数（ロボットが今どんな状態かを記録する）
# ==============================================================
last_interaction = time.time()   # 最後に操作された時刻（放置検知に使う）
busy             = False         # True のとき: アニメーション中（新しい操作を受け付けない）
servo_l_angle    = 90            # 現在のサーボ角度（0〜180度）
current_mode     = MODE_NORMAL   # ホーム画面で選ばれているモード
app_state        = "home"        # 今どの画面にいるか
# app_state の種類:
#   "home"            ... ホーム画面
#   "timer_select"    ... タイマー時間選択中
#   "timer_running"   ... タイマー動作中
#   "game_waiting"    ... ゲーム開始待ち（タップを待っている）
#   "game_running"    ... ゲームカウント中（タップで停止）
#   "settings_select" ... 設定サブメニュー選択中
#   "color_select"    ... 色設定中
#   "volume_select"   ... 音量設定中

_game_touch   = None    # ゲームのタップ検知（main() で初期化）
timer_cancel  = False   # タイマーをキャンセルするフラグ
game_cancel   = False   # ゲームをキャンセルするフラグ
color_channel = 0       # 今どの色チャンネルを設定中か（0=R, 1=G, 2=B）
color_edit: list = [0, 30, 60]  # 編集中の色（main() で NORMAL_COLOR から初期化）


# ==============================================================
# 【1-7】 設定の保存・読み込み
# ==============================================================

def load_settings():
    """フラッシュメモリから色・音量設定を読み込む。
    ファイルがない場合はデフォルト値を返す。"""
    try:
        with open(SETTINGS_FILE) as f:
            d = json.load(f)
        color  = tuple(d.get("normal_color", [0, 30, 60]))
        v      = int(d.get("volume", 67))
        # 保存された音量を VOLUME_LEVELS の一番近い値にそろえる
        volume = min(VOLUME_LEVELS, key=lambda x: abs(x - v))
        return color, volume
    except:
        return (0, 30, 60), 67   # 読み込み失敗 → デフォルト値

def save_settings():
    """現在の色・音量設定をフラッシュメモリに保存する"""
    with open(SETTINGS_FILE, "w") as f:
        json.dump({"normal_color": list(NORMAL_COLOR), "volume": VOLUME}, f)


# ==============================================================
# 【2-1】 サーボ（翼）の制御
# ==============================================================

def set_angle(angle):
    """サーボを指定した角度（0〜180）にする。
    duty_ns はパルス幅をナノ秒で指定する方法。
    500000ns(0.5ms)=0度、2500000ns(2.5ms)=180度。"""
    servo_l.duty_ns(int(500_000 + (angle / 180) * 2_000_000))

async def move_servo(target, step=3, delay=0.02):
    """サーボを現在の角度から target までなめらかに動かす。
    step  : 一度に動かす角度（大きいほど速い）
    delay : 1ステップごとの待ち時間（秒）"""
    global servo_l_angle
    current = servo_l_angle
    while abs(target - current) > step:
        current += step if target > current else -step
        set_angle(current)
        await asyncio.sleep(delay)   # 他のタスクに制御を渡しながら待つ
    set_angle(target)
    servo_l_angle = target

async def servo_neutral():
    """翼を中央（90度）に戻す"""
    await move_servo(90)

async def wing_flap(times=3):
    """翼を上下に times 回パタパタさせる"""
    for _ in range(times):
        await move_servo(45, step=5)    # 上げる（速め）
        await move_servo(135, step=5)   # 下げる（速め）
    await servo_neutral()

async def wing_wave():
    """翼をゆっくり持ち上げてから戻す（ふわっとした動き）"""
    await move_servo(45, step=2)
    await asyncio.sleep(0.3)
    await move_servo(135, step=2)
    await asyncio.sleep(0.3)
    await servo_neutral()

async def dance():
    """翼を4回素早く上下させる（ダンス）"""
    for _ in range(4):
        await move_servo(45, step=4)
        await move_servo(135, step=4)
    await servo_neutral()


# ==============================================================
# 【2-2】 LED（目）の制御
# ==============================================================

def set_eyes(r, g, b):
    """両目を同じ色にする。r・g・b は 0〜255。
    duty_u16 は 0〜65535 で明るさを指定するので、255で割って65535を掛けて変換する。"""
    for ch, v in ((L_R, r), (L_G, g), (L_B, b),
                  (R_R, r), (R_G, g), (R_B, b)):
        ch.duty_u16(int(v / 255 * 65535))

def set_eye_l(r, g, b):
    """左目だけ色を変える"""
    for ch, v in ((L_R, r), (L_G, g), (L_B, b)):
        ch.duty_u16(int(v / 255 * 65535))

def set_eye_r(r, g, b):
    """右目だけ色を変える"""
    for ch, v in ((R_R, r), (R_G, g), (R_B, b)):
        ch.duty_u16(int(v / 255 * 65535))

def eyes_off():
    """両目を消す（黒 = 消灯）"""
    set_eyes(0, 0, 0)

# --- LED アニメーション ---

async def led_pink_sparkle():
    """ピンク→白 を4回交互に点滅（喜んでいる演出）"""
    for _ in range(4):
        set_eyes(255, 80, 120); await asyncio.sleep(0.15)
        set_eyes(255, 255, 255); await asyncio.sleep(0.15)

async def led_rainbow():
    """虹色（赤→橙→黄→緑→青→紫）に順番に光らせる"""
    colors = [(255,0,0), (255,127,0), (255,255,0),
              (0,255,0), (0,0,255),   (148,0,211)]
    for _ in range(2):
        for c in colors:
            set_eyes(*c); await asyncio.sleep(0.15)

async def led_decision_blink():
    """白→消灯 を2回点滅（決定演出）"""
    for _ in range(2):
        set_eyes(255, 255, 255); await asyncio.sleep(0.08)
        eyes_off();               await asyncio.sleep(0.08)

async def led_fade_out():
    """オレンジ色からゆっくり暗くなる（フェードアウト）"""
    for v in range(255, 0, -5):
        set_eyes(v, int(v * 0.6), int(v * 0.2))
        await asyncio.sleep(0.03)

async def led_lonely():
    """青をゆっくり明るく→暗くを3回繰り返す（寂しそうな演出）"""
    for _ in range(3):
        for v in range(0, 150, 8):
            set_eyes(0, 0, v); await asyncio.sleep(0.02)
        for v in range(150, 0, -8):
            set_eyes(0, 0, v); await asyncio.sleep(0.02)

async def led_intense_flash(r, g, b, times=8):
    """白→指定色 を times 回激しく点滅（タイマー終了演出）"""
    for _ in range(times):
        set_eyes(255, 255, 255); await asyncio.sleep(0.08)
        set_eyes(r, g, b);       await asyncio.sleep(0.08)


# ==============================================================
# 【2-3】 ブザー（音）の制御
# ==============================================================
# パッシブブザーは duty 50%（duty_u16 = 32768）が最大音量。
# VOLUME=100 → duty 32768（50%）= 最大
# VOLUME= 67 → duty 21954（33%）= 中
# VOLUME= 33 → duty 10814（16%）= 小
# VOLUME=  0 → duty     0（ 0%）= 無音

def _buzz_duty():
    """現在の VOLUME 設定に合わせたデューティ値（0〜32768）を返す"""
    return int(32768 * VOLUME / 100)

async def beep(freq=1000, ms=100):
    """指定した周波数で ms ミリ秒だけ音を鳴らす汎用関数
    freq : 音の高さ（Hz）。低いほど低音、高いほど高音
    ms   : 音を鳴らす時間（ミリ秒）"""
    buzzer.freq(freq)
    buzzer.duty_u16(_buzz_duty())
    await asyncio.sleep(ms / 1000)
    buzzer.duty_u16(0)

# --- ブザーアニメーション ---

async def chirp():
    """シマエナガ風鳴き声（周波数を上げてから下げるスイープ音）"""
    for freq in range(2000, 3800, 150):
        buzzer.freq(freq); buzzer.duty_u16(_buzz_duty())
        await asyncio.sleep(0.015)
    buzzer.duty_u16(0)
    await asyncio.sleep(0.04)
    for freq in range(3500, 2200, -150):
        buzzer.freq(freq); buzzer.duty_u16(_buzz_duty())
        await asyncio.sleep(0.015)
    buzzer.duty_u16(0)

async def happy_chirp():
    """撫でられたときの喜ぶ鳴き声（高音が上がっていく感じ）"""
    for freq in [3200, 3800, 4400, 5000]:
        await beep(freq, 45); await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)
    for freq in [3600, 4200, 5000]:
        await beep(freq, 35); await asyncio.sleep(0.015)

async def bird_call():
    """シマエナガらしいピッピッピッ（起動時の鳴き声）"""
    for _ in range(3):
        for f in range(3200, 4600, 350):
            buzzer.freq(f); buzzer.duty_u16(_buzz_duty())
            await asyncio.sleep(0.008)
        buzzer.duty_u16(0)
        await asyncio.sleep(0.07)

async def startup_jingle():
    """通常起動時のジングル（ドレミソ↑ の4音）"""
    for freq, ms in [(1319, 80), (1568, 80), (1976, 80), (2637, 180)]:
        buzzer.freq(freq); buzzer.duty_u16(_buzz_duty())
        await asyncio.sleep(ms / 1000)
        buzzer.duty_u16(0); await asyncio.sleep(0.03)

async def wifi_off_jingle():
    """WiFiオフ起動時のジングル（ピッピッ＋低音ボン）"""
    await beep(2800, 70); await asyncio.sleep(0.05)
    await beep(2800, 70); await asyncio.sleep(0.05)
    await beep(700, 350)

async def beep_mode_change():
    """つまみを回してモードが変わったときの短いクリック音"""
    await beep(2200, 35)

async def beep_decision():
    """3秒長押しで決定したときの確認音（2音）"""
    await beep(2000, 70); await asyncio.sleep(0.03)
    await beep(2800, 90)

async def timer_end_fanfare():
    """タイマー終了時のファンファーレ（4音）"""
    for freq, ms in [(1047, 120), (1319, 120), (1568, 120), (2093, 400)]:
        buzzer.freq(freq); buzzer.duty_u16(_buzz_duty())
        await asyncio.sleep(ms / 1000)
        buzzer.duty_u16(0); await asyncio.sleep(0.04)


# ==============================================================
# 【3-1】 つまみ（可変抵抗）の読み取り
# ==============================================================
# pot.read_u16() は 0〜65535 の数値を返す。
#   0     = つまみを左いっぱい
#   65535 = つまみを右いっぱい

def read_mode():
    """つまみの位置からホーム画面のモードを返す（4ゾーン）"""
    val = pot.read_u16()
    if   val < 16384:  return MODE_COLOR    # 左端 → 設定
    elif val < 32768:  return MODE_TIMER    # 中左 → タイマー
    elif val < 49152:  return MODE_NORMAL   # 中右 → 通常
    else:              return MODE_GAME_10S # 右端 → ゲーム

def read_timer_selection():
    """タイマー時間選択画面でのつまみ位置（4ゾーン）"""
    val = pot.read_u16()
    if   val < 16384:  return TIMER_HOME   # 左端 → ホームに戻る
    elif val < 32768:  return 60           # 中左 → 1分
    elif val < 49152:  return 180          # 中右 → 3分
    else:              return 300          # 右端 → 5分

def read_settings_selection():
    """設定サブメニューでのつまみ位置（3ゾーン）"""
    val = pot.read_u16()
    if   val < 21845:  return SETTINGS_HOME    # 左 → ホームに戻る
    elif val < 43690:  return SETTINGS_COLOR   # 中 → 色設定
    else:              return SETTINGS_VOL     # 右 → 音量設定

def read_channel_value():
    """色設定でつまみの値を 0〜255 に変換する"""
    return int(pot.read_u16() / 65535 * 255)

def read_volume_value():
    """音量設定でつまみの位置から音量段階を返す（4ゾーン）"""
    val = pot.read_u16()
    if   val < 16384:  return 0    # 左端 → 無音
    elif val < 32768:  return 33   # 中左 → 大
    elif val < 49152:  return 67   # 中右 → 中
    else:              return 100  # 右端 → 小（duty 50% = 最大音量）


# ==============================================================
# 【3-2】 LED プレビュー表示（画面ごとの目の色）
# ==============================================================

def show_home_preview(mode):
    """ホーム画面でつまみが示すモードの色を目で表示する"""
    if   mode == MODE_NORMAL:   set_eyes(*NORMAL_COLOR)   # 通常色
    elif mode == MODE_TIMER:    set_eyes(50, 50, 10)      # 薄い黄
    elif mode == MODE_GAME_10S: set_eyes(0, 45, 63)       # 薄いシアン
    else:                       set_eyes(*NORMAL_COLOR)   # 設定 → 現在の色

def show_timer_preview(selection):
    """タイマー選択画面でつまみが示す時間の色を目で表示する"""
    if selection == TIMER_HOME:
        set_eyes(63, 20, 30)   # 薄いピンク（戻る）
    else:
        r, g, b = TIMER_COLORS[selection]
        set_eyes(r // 2, g // 2, b // 2)   # タイマー色を半輝度で表示

def show_settings_preview(sel):
    """設定サブメニューでつまみが示す項目の色を目で表示する"""
    if   sel == SETTINGS_HOME:  set_eyes(63, 20, 30)      # 薄いピンク（戻る）
    elif sel == SETTINGS_COLOR: set_eyes(*NORMAL_COLOR)   # 現在の色
    else:
        v = int(VOLUME / 100 * 200)
        set_eyes(v, v // 2, 0)   # オレンジ（音量に比例した明るさ）


# ==============================================================
# 【3-3】 画面遷移・アニメーションのヘルパー
# ==============================================================

def go_home():
    """ホーム画面に戻る（app_state を "home" にしてLEDを更新）"""
    global app_state
    app_state = "home"
    show_home_preview(current_mode)
    log("[ナビ] ホーム画面に戻った")

async def _run(fn):
    """busy フラグを立てて→アニメーション実行→通常色に戻す、をまとめた関数。
    busy が True（他のアニメーション中）のときは何もしない。"""
    global busy, last_interaction
    if busy:
        return
    busy = True
    last_interaction = time.time()
    await fn()
    set_eyes(*NORMAL_COLOR)   # アニメーション後は通常色に戻す
    await servo_neutral()     # 翼を中央に戻す
    busy = False


# ==============================================================
# 【4-1】 リアクション（ロボットの反応）
# ==============================================================

async def react_touch():
    """撫でられたときの反応: ピンクキラキラ + 羽パタパタ + 喜び鳴き声"""
    async def _():
        await asyncio.gather(led_pink_sparkle(), wing_flap(3), happy_chirp())
    await _run(_)

async def react_lonely():
    """60秒放置されたときの反応: 青くゆっくり光る（寂しいよ〜）
    busy は使わないので、この間もつまみ操作ができる。"""
    global last_interaction
    last_interaction = time.time()   # 次の発動を遅らせるためにリセット
    await led_lonely()
    set_eyes(*NORMAL_COLOR)

async def react_dance():
    """ランダムで踊る: 虹色 + ダンス"""
    async def _():
        await asyncio.gather(led_rainbow(), dance())
    await _run(_)


# ==============================================================
# 【4-2】 タイマー機能
# ==============================================================

async def run_timer(duration):
    """指定した秒数（60/180/300）のタイマーを動かす。
    残り時間に合わせて目の明るさが変化し、終了するとお知らせする。
    POT を左端にして3秒長押しするとキャンセルできる。"""
    global busy, app_state, timer_cancel
    busy = True
    timer_cancel = False
    app_state = "timer_running"
    r, g, b = TIMER_COLORS[duration]

    await wing_flap(1)   # スタートの合図で1回パタパタ
    start        = time.time()
    blink_on     = True
    last_print_s = -1

    while True:
        remaining = duration - (time.time() - start)
        if remaining <= 0 or timer_cancel:
            break

        # ログに残り時間を表示（30秒ごと、残り10秒以下は毎秒）
        s = int(remaining)
        if s != last_print_s and (s <= 10 or s % 30 == 0):
            log(f"[タイマー] 残り {s // 60:02d}:{s % 60:02d}")
            last_print_s = s

        # 残り時間が減るほど目が暗くなる（最小 15% の明るさ）
        ratio = max(remaining / duration, 0.15)
        if blink_on:
            set_eyes(int(r * ratio), int(g * ratio), int(b * ratio))
        else:
            eyes_off()
        blink_on = not blink_on
        await asyncio.sleep(0.5)

    if timer_cancel:
        log("[タイマー] キャンセル → ホームに戻る")
        set_eyes(*NORMAL_COLOR)
    else:
        log("[タイマー] 終了！")
        await asyncio.gather(timer_end_fanfare(), led_intense_flash(r, g, b))
        await asyncio.gather(led_pink_sparkle(), wing_flap(3))
        set_eyes(*NORMAL_COLOR)

    app_state = "home"
    busy = False


# ==============================================================
# 【4-3】 十秒あてゲーム
# ==============================================================

async def run_game_10s():
    """ちょうど10秒を体感で当てるゲーム。
    フェーズ1: シアン点滅で待機 → タップで計測スタート
    フェーズ2: 計測中 → もう一度タップで停止・判定
    誤差が小さいほど豪華な演出になる。"""
    global busy, app_state, game_cancel
    busy = True
    game_cancel = False
    if _game_touch is not None:
        _game_touch.clear()

    # --- フェーズ1: タップで開始を待つ ---
    app_state = "game_waiting"
    log("[ゲーム] タップして開始！")
    blink_step = 0
    while _game_touch is not None and not _game_touch.is_set() and not game_cancel:
        blink_step = (blink_step + 1) % 12   # 0〜11 でループ（6回点灯・6回消灯）
        set_eyes(0, 180, 255) if blink_step < 6 else eyes_off()
        await asyncio.sleep(0.05)

    if game_cancel:
        log("[ゲーム] キャンセル")
        set_eyes(*NORMAL_COLOR); await servo_neutral()
        app_state = "home"; busy = False
        return

    if _game_touch is not None:
        _game_touch.clear()

    # --- フェーズ2: 計測開始（緑で合図） ---
    app_state = "game_running"
    set_eyes(0, 255, 0); await asyncio.sleep(0.05); eyes_off()
    start_ms = time.ticks_ms()   # ミリ秒精度で計測開始

    while _game_touch is not None and not _game_touch.is_set() and not game_cancel:
        await asyncio.sleep(0.05)

    if game_cancel:
        log("[ゲーム] キャンセル")
        set_eyes(*NORMAL_COLOR); await servo_neutral()
        app_state = "home"; busy = False
        return

    # --- 結果判定 ---
    elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ms)
    elapsed    = elapsed_ms / 1000           # ミリ秒 → 秒
    error      = abs(elapsed - 10.0)         # 10秒との誤差
    err_r      = round(error * 10) / 10      # 小数第1位に丸める

    log(f"[ゲーム] 経過 {elapsed:.1f}秒 / 誤差 {err_r:.1f}秒")

    if error < 0.1:
        log("[ゲーム] 神！完璧！")
        await asyncio.gather(led_rainbow(), wing_flap(5))
        await asyncio.gather(led_rainbow(), wing_flap(5))
    elif error < 0.5:
        log("[ゲーム] 大成功！")
        await asyncio.gather(led_rainbow(), wing_flap(4))
    elif error < 1.0:
        log("[ゲーム] 成功！")
        await asyncio.gather(led_pink_sparkle(), wing_flap(3))
    elif error < 2.0:
        log("[ゲーム] まあまあ…")
        await asyncio.gather(led_pink_sparkle(), wing_flap(2))
    else:
        log("[ゲーム] 失敗…")
        set_eyes(180, 0, 0); await asyncio.sleep(1.5)

    set_eyes(*NORMAL_COLOR)
    await servo_neutral()
    app_state = "home"
    busy = False


# ==============================================================
# 【5-1】 タッチセンサーの監視タスク
# ==============================================================
# このタスクは常にバックグラウンドで動き続ける。
# 0.05秒ごとにセンサーを確認し、タップ・長押しを判別する。

async def touch_task():
    global app_state, current_mode, last_interaction
    global timer_cancel, game_cancel, NORMAL_COLOR, color_channel, VOLUME

    press_start        = None    # 押し始めた時刻（None = 今は押していない）
    decision_triggered = False   # 3秒長押し決定がもう発動したかどうか
    tap_count          = 0       # 短タップの連続回数
    last_tap_time      = 0       # 最後にタップした時刻
    prev_pot_zone      = None    # POT のゾーン変化検知用

    def current_pot_zone():
        """現在の画面でのつまみゾーン番号を返す（長押し中に動かしたか検知用）"""
        if app_state in ("home", "timer_select", "settings_select"):
            return pot.read_u16() // 20000
        elif app_state in ("timer_running", "game_waiting", "game_running"):
            return pot.read_u16() < 8192   # 左端かどうか（True/False）
        elif app_state == "color_select":
            return (color_channel, pot.read_u16() // 8192)
        elif app_state == "volume_select":
            return pot.read_u16() // 4096
        return None

    async def wait_for_release():
        """タッチセンサーから指が完全に離れるまで待つ"""
        while touch.value() == 1:
            await asyncio.sleep(0.05)

    while True:
        now = time.time()

        if touch.value() == 1:
            # ===== 押している間の処理 =====

            if press_start is None:
                # 押し始めた瞬間の処理
                press_start        = now
                decision_triggered = False
                prev_pot_zone      = current_pot_zone()

                # ゲーム中はタップした瞬間に開始・停止
                if app_state in ("game_waiting", "game_running") and _game_touch is not None:
                    _game_touch.set()
                    decision_triggered = True   # 長押しキャンセルが誤発動しないよう保持
            else:
                # 押し続けている間: つまみが動いたら3秒をリセット
                if not decision_triggered:
                    zone = current_pot_zone()
                    if zone != prev_pot_zone:
                        prev_pot_zone = zone
                        press_start   = now
                        log("[タッチ] POT変更 → 3秒リセット")

                held = now - press_start   # 押し続けた時間（秒）

                # 3秒以上押し続けたら「決定」
                can_decide = (not busy or app_state in ("timer_running", "game_waiting", "game_running"))
                if held >= 3.0 and not decision_triggered and can_decide:
                    decision_triggered = True
                    last_interaction   = now
                    log(f"[タッチ] 3秒長押し決定 (画面:{app_state} / モード:{current_mode})")
                    await asyncio.gather(led_decision_blink(), beep_decision())

                    # ---- 画面ごとの「決定」処理 ----

                    if app_state == "home":
                        if current_mode == MODE_NORMAL:
                            await react_touch()
                            await wait_for_release()
                            press_start = None; decision_triggered = False
                        elif current_mode == MODE_TIMER:
                            app_state = "timer_select"
                            show_timer_preview(read_timer_selection())
                            log("[ナビ] タイマー時間選択画面へ")
                        elif current_mode == MODE_GAME_10S:
                            log("[ゲーム] 十秒あてゲーム 準備中")
                            asyncio.create_task(run_game_10s())
                            await wait_for_release()
                            press_start = None; decision_triggered = False
                        elif current_mode == MODE_COLOR:
                            app_state = "settings_select"
                            show_settings_preview(read_settings_selection())
                            log("[ナビ] 設定サブメニューへ")

                    elif app_state == "timer_select":
                        selection = read_timer_selection()
                        if selection == TIMER_HOME:
                            log("[ナビ] ホームに戻る")
                            go_home()
                        else:
                            log(f"[タイマー] 開始: {selection}秒")
                            asyncio.create_task(run_timer(selection))
                        await wait_for_release()
                        press_start = None; decision_triggered = False

                    elif app_state == "timer_running":
                        if pot.read_u16() < 8192:   # つまみが左端 → キャンセル確定
                            log("[タイマー] キャンセル確定")
                            timer_cancel = True
                        await wait_for_release()
                        press_start = None; decision_triggered = False

                    elif app_state in ("game_waiting", "game_running"):
                        if pot.read_u16() < 8192:   # つまみが左端 → キャンセル確定
                            log("[ゲーム] キャンセル確定")
                            game_cancel = True
                        await wait_for_release()
                        press_start = None; decision_triggered = False

                    elif app_state == "color_select":
                        # 今のチャンネルの値を確定して次へ（R→G→B→保存）
                        color_edit[color_channel] = read_channel_value()
                        color_channel += 1
                        if color_channel >= 3:
                            NORMAL_COLOR  = tuple(color_edit)
                            color_channel = 0
                            save_settings()
                            log(f"[色設定] 保存完了: RGB{NORMAL_COLOR}")
                            go_home()
                        else:
                            names = ["R(赤)", "G(緑)", "B(青)"]
                            log(f"[色設定] 次: {names[color_channel]} を設定 → 3秒長押しで確定")
                        await wait_for_release()
                        press_start = None; decision_triggered = False

                    elif app_state == "settings_select":
                        sel = read_settings_selection()
                        if sel == SETTINGS_HOME:
                            log("[ナビ] ホームに戻る")
                            go_home()
                        elif sel == SETTINGS_COLOR:
                            app_state     = "color_select"
                            color_channel = 0
                            color_edit[:] = list(NORMAL_COLOR)
                            set_eyes(*color_edit)
                            log("[色設定] R(赤)を設定 → 3秒長押しで確定")
                        else:
                            app_state = "volume_select"
                            log(f"[音量設定] 現在: {VOLUME_NAMES[VOLUME_LEVELS.index(VOLUME)]} → POTで調整 → 3秒長押しで保存")
                        await wait_for_release()
                        press_start = None; decision_triggered = False

                    elif app_state == "volume_select":
                        VOLUME = read_volume_value()
                        save_settings()
                        log(f"[音量設定] 保存: {VOLUME_NAMES[VOLUME_LEVELS.index(VOLUME)]}")
                        go_home()
                        await wait_for_release()
                        press_start = None; decision_triggered = False

        else:
            # ===== 離したときの処理 =====
            if press_start is not None:
                held = now - press_start
                # 短タップ（1秒未満・決定が発動していない）の処理
                if held < 1.0 and not decision_triggered:
                    if app_state == "home" and current_mode == MODE_NORMAL and not busy:
                        # 4秒以内に3回タップ → なでなで
                        if now - last_tap_time < 4.0:
                            tap_count += 1
                        else:
                            tap_count = 1
                        last_tap_time = now
                        log(f"[タッチ] タップ {tap_count}/3")
                        if tap_count >= 3:
                            tap_count = 0
                            last_interaction = now
                            await react_touch()

                press_start        = None
                decision_triggered = False

        await asyncio.sleep(0.05)


# ==============================================================
# 【5-2】 つまみの監視タスク
# ==============================================================
# このタスクも常にバックグラウンドで動き続ける。
# 0.1秒ごとにつまみの値を確認し、LED の色を更新したりモードを切り替える。

async def pot_monitor_task():
    global current_mode

    prev_timer_sel    = None   # タイマー選択の変化検知用
    prev_settings_sel = None   # 設定選択の変化検知用
    prev_vol_sel      = None   # 音量選択の変化検知用

    while True:
        if app_state == "timer_running":
            # タイマー動作中: つまみを左端に向けるとキャンセル候補（ピンク）
            if pot.read_u16() < 8192:
                set_eyes(63, 20, 30)

        elif app_state in ("game_waiting", "game_running"):
            # ゲーム中: つまみを左端に向けるとキャンセル候補（赤）
            if pot.read_u16() < 8192:
                set_eyes(180, 0, 0)

        elif app_state == "color_select":
            # 色設定中: つまみをリアルタイムで目に反映
            color_edit[color_channel] = read_channel_value()
            set_eyes(*color_edit)

        elif app_state == "volume_select":
            # 音量設定中: つまみでオレンジの明るさが変わる（プレビュー）
            v = read_volume_value()
            set_eyes(int(v * 2), int(v), 0)
            if v != prev_vol_sel:
                prev_vol_sel = v
                log(f"[音量設定] {VOLUME_NAMES[VOLUME_LEVELS.index(v)]}")
                if v > 0:
                    buzzer.freq(2200)
                    buzzer.duty_u16(int(32768 * v / 100))
                    await asyncio.sleep(0.035)
                    buzzer.duty_u16(0)

        elif not busy:
            # アニメーション中でない通常時
            if app_state == "home":
                mode = read_mode()
                if mode != current_mode:
                    current_mode = mode
                    _order = [MODE_COLOR, MODE_TIMER, MODE_NORMAL, MODE_GAME_10S]
                    labels = [f"[{MODE_NAMES[m]}]" if m == mode else MODE_NAMES[m] for m in _order]
                    log(f"[モード] {' / '.join(labels)}")
                    show_home_preview(mode)
                    await beep_mode_change()

            elif app_state == "timer_select":
                sel = read_timer_selection()
                show_timer_preview(sel)
                if sel != prev_timer_sel:
                    prev_timer_sel = sel
                    labels = [f"[{TIMER_LABELS[k]}]" if k == sel else TIMER_LABELS[k] for k in TIMER_ORDER]
                    log(f"[タイマー選択] {' / '.join(labels)}")
                    await beep_mode_change()

            elif app_state == "settings_select":
                sel = read_settings_selection()
                show_settings_preview(sel)
                if sel != prev_settings_sel:
                    prev_settings_sel = sel
                    labels = [f"[{SETTINGS_LABELS[k]}]" if k == sel else SETTINGS_LABELS[k] for k in SETTINGS_ORDER]
                    log(f"[設定] {' / '.join(labels)}")
                    await beep_mode_change()

        await asyncio.sleep(0.1)


# ==============================================================
# 【5-3】 放置検知タスク
# ==============================================================
# 通常モードで60秒間何も操作がなかったら「寂しい」反応をする。
# ランダムで気まぐれに羽ばたきもする（約0.5%/秒）。

async def idle_task():
    global last_interaction
    while True:
        idle_sec = time.time() - last_interaction
        if idle_sec > 60 and current_mode == MODE_NORMAL and app_state == "home":
            last_interaction = time.time()   # リセット（連続発動を防ぐ）
            log("[アイドル] 60秒放置 → 寂しいモード")
            try:
                await react_lonely()
            except Exception as e:
                log(f"[アイドル] エラー: {e}")
        elif not busy and random.random() < 0.005:
            action = random.choice([wing_wave, wing_flap])
            try:
                await action()
            except Exception:
                pass
        await asyncio.sleep(1)


# ==============================================================
# 【6-1】 WiFi アクセスポイントの起動
# ==============================================================

def setup_wifi_ap():
    """ESP32 を WiFi アクセスポイントとして起動する。
    スマホでこの AP に接続すると http://192.168.4.1/ でロボットを操作できる。"""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=3)
    for _ in range(20):
        if ap.active():
            break
        time.sleep(0.5)
    ip = ap.ifconfig()[0]
    log(f"[WiFi] AP起動: SSID={AP_SSID}  PW={AP_PASSWORD}")
    log(f"[WiFi] ブラウザで http://{ip}/ を開いてください")


# ==============================================================
# 【6-2】 Web ページ（HTML）の定義
# ==============================================================
# HTTP レスポンスとして直接送信する HTML 文字列。
# 省メモリのためミニファイ（改行・スペースを除いた）形式で書いている。

_HOME_HTML = (
    'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
    '<!DOCTYPE html><html><head>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Shimaenaga</title>'
    '<style>*{box-sizing:border-box}body{margin:0;background:#111;color:#0f0;'
    'font:14px/1.6 sans-serif;text-align:center;padding:20px}'
    'h1{color:#afa;font-size:22px;margin:16px 0 4px}p{color:#666;margin:0 0 32px}'
    'a{display:block;margin:0 auto 12px;padding:18px;width:90%;max-width:280px;'
    'background:#1a1a1a;color:#0f0;text-decoration:none;border:1px solid #333;'
    'border-radius:10px;font-size:16px}'
    '</style></head><body>'
    '<h1>Shimaenaga</h1><p>シマエナガロボット</p>'
    '<a href="/logs">ログ表示</a>'
    '<a href="/ctrl">ロボット操作</a>'
    '<a href="/color">カラー設定</a>'
    '<a href="/volume">音量設定</a>'
    '</body></html>'
).encode('utf-8')

_LOG_PAGE = (
    'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
    '<!DOCTYPE html><html><head>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Shimaenaga Log</title>'
    '<style>*{box-sizing:border-box}'
    'body{margin:0;background:#111;color:#0f0;font:13px/1.5 monospace;'
    'height:100vh;display:flex;flex-direction:column}'
    'h1{margin:8px;font-size:15px;color:#afa;flex-shrink:0}'
    'a{margin:0 8px 4px;color:#555;font-size:12px;text-decoration:none;flex-shrink:0}'
    '#g{flex:1;padding:8px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}'
    '</style></head><body>'
    '<h1>Shimaenaga Log</h1><a href="/">< ホームに戻る</a>'
    '<div id="g"></div>'
    '<script>'
    'var g=document.getElementById("g"),s=1,es=null;'
    'g.addEventListener("scroll",function(){s=g.scrollTop+g.clientHeight>=g.scrollHeight-20?1:0});'
    'function addLine(t){'
    'var d=document.createElement("div");d.textContent=t;g.appendChild(d);'
    'if(s)g.scrollTop=g.scrollHeight}'
    'function conn(){'
    'if(es)try{es.close()}catch(e){}'
    'while(g.firstChild)g.removeChild(g.firstChild);'
    'es=new EventSource("/sse");'
    'es.onmessage=function(e){addLine(e.data)};'
    'es.onerror=function(){es.close();setTimeout(conn,3000);}}'
    'document.addEventListener("visibilitychange",function(){'
    'if(!document.hidden&&(!es||es.readyState===2))conn()});'
    'conn();'
    '</script></body></html>'
).encode('utf-8')

_COLOR_TMPL = (
    'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
    '<!DOCTYPE html><html><head>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>カラー設定</title>'
    '<style>*{box-sizing:border-box}body{margin:0;background:#111;color:#0f0;'
    'font:14px/1.5 sans-serif;padding:16px}'
    'h1{color:#afa;font-size:16px;margin:0 0 16px}'
    '.row{display:flex;align-items:center;gap:10px;margin-bottom:12px}'
    'label{width:20px;color:#888}'
    'input[type=range]{flex:1;accent-color:#0f0}'
    '.val{width:32px;text-align:right;font-size:13px}'
    '#prev{width:60px;height:60px;border-radius:50%;border:2px solid #333;margin:12px auto}'
    'button{display:block;width:100%;padding:14px;margin-top:12px;'
    'background:#1a3a1a;color:#0f0;border:1px solid #2a5a2a;border-radius:8px;font-size:15px;cursor:pointer}'
    'a{display:block;text-align:center;margin-top:12px;color:#555;font-size:12px;text-decoration:none}'
    '</style></head><body>'
    '<h1>カラー設定</h1>'
    '<div id="prev"></div>'
    '<div class="row"><label>R</label><input type="range" id="r" min="0" max="255" value="%d" oninput="upd()"><span class="val" id="rv">%d</span></div>'
    '<div class="row"><label>G</label><input type="range" id="g" min="0" max="255" value="%d" oninput="upd()"><span class="val" id="gv">%d</span></div>'
    '<div class="row"><label>B</label><input type="range" id="b" min="0" max="255" value="%d" oninput="upd()"><span class="val" id="bv">%d</span></div>'
    '<button onclick="sv()">この色で保存</button>'
    '<a href="/">戻る</a>'
    '<script>'
    'function upd(){'
    'var r=document.getElementById("r").value,'
    'g=document.getElementById("g").value,'
    'b=document.getElementById("b").value;'
    'document.getElementById("rv").textContent=r;'
    'document.getElementById("gv").textContent=g;'
    'document.getElementById("bv").textContent=b;'
    'document.getElementById("prev").style.background="rgb("+r+","+g+","+b+")";'
    'var x=new XMLHttpRequest();x.open("GET","/set-color?r="+r+"&g="+g+"&b="+b);x.send()}'
    'function sv(){'
    'var r=document.getElementById("r").value,'
    'g=document.getElementById("g").value,'
    'b=document.getElementById("b").value;'
    'var x=new XMLHttpRequest();x.open("GET","/set-color?r="+r+"&g="+g+"&b="+b+"&save=1");x.send();'
    'alert("保存しました！");}'
    'upd();'
    '</script></body></html>'
)

_VOLUME_TMPL = (
    'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
    '<!DOCTYPE html><html><head>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>音量設定</title>'
    '<style>*{box-sizing:border-box}body{margin:0;background:#111;color:#0f0;'
    'font:14px/1.5 sans-serif;padding:16px;text-align:center}'
    'h1{color:#afa;font-size:16px;margin:0 0 24px}'
    '.btn{display:block;width:100%%;padding:18px;margin-bottom:12px;'
    'background:#1a1a1a;color:#0f0;border:2px solid #333;border-radius:10px;font-size:18px;cursor:pointer}'
    '.btn.act{border-color:#0f0;background:#1a3a1a}'
    'a{display:block;margin-top:16px;color:#555;font-size:12px;text-decoration:none}'
    '</style></head><body>'
    '<h1>音量設定</h1>'
    '<button class="btn%s" data-v="33"  onclick="sv(33)" >大</button>'
    '<button class="btn%s" data-v="67"  onclick="sv(67)" >中</button>'
    '<button class="btn%s" data-v="100" onclick="sv(100)">小</button>'
    '<button class="btn%s" data-v="0"   onclick="sv(0)"  >無音</button>'
    '<a href="/">戻る</a>'
    '<script>'
    'function sv(v){'
    'document.querySelectorAll(".btn").forEach(function(b){b.classList.remove("act")});'
    'event.target.classList.add("act");'
    'var x=new XMLHttpRequest();x.open("GET","/set-volume?v="+v);x.send()}'
    '</script></body></html>'
)

_CTRL_PAGE = (
    'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    'Cache-Control: no-store\r\nConnection: close\r\n\r\n'
    '<!DOCTYPE html><html><head>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>ロボット操作</title>'
    '<style>*{box-sizing:border-box}'
    'body{margin:0;background:#111;color:#0f0;font:14px/1.5 sans-serif;padding:0 12px 24px}'
    'h1{color:#afa;font-size:16px;margin:14px 0 4px}'
    'h2{color:#8f8;font-size:13px;margin:14px 0 6px;border-bottom:1px solid #333;padding-bottom:3px}'
    '.clr{display:flex;gap:8px;margin:6px 0 12px}'
    '.cb{width:44px;height:44px;border-radius:50%;border:3px solid #333;cursor:pointer;'
    'display:flex;align-items:center;justify-content:center;color:#666;font-size:12px}'
    '.cb.act{border-color:#fff}'
    'label{color:#888;font-size:12px;display:block}'
    'input[type=range]{width:100%;accent-color:#0f0;margin:4px 0 12px}'
    '.wk,.bk{margin:0;outline:none;-webkit-tap-highlight-color:transparent}'
    '.wk{position:absolute;top:0;width:12.4%;height:100%;background:#e8e8e8;'
    'border:none;border-right:1px solid #888;display:flex;align-items:flex-end;'
    'justify-content:center;padding-bottom:3px;font-size:8px;color:#555;cursor:pointer;user-select:none}'
    '.wk.p,.wk:active{background:#aad}'
    '.bk{position:absolute;top:0;width:7.5%;height:62%;background:#222;'
    'border:1px solid #555;border-radius:0 0 3px 3px;z-index:1;cursor:pointer}'
    '.bk.p,.bk:active{background:#558}'
    '.snd-row{display:flex;gap:8px;margin:6px 0 12px}'
    '.sb{background:#1a1a1a;color:#0f0;border:1px solid #333;border-radius:8px;'
    'padding:12px 0;flex:1;font-size:13px;cursor:pointer;-webkit-tap-highlight-color:transparent}'
    '.sb:active{background:#2a2a2a}'
    'a{display:block;text-align:center;margin-top:16px;color:#555;font-size:12px;text-decoration:none}'
    '</style></head><body>'
    '<h1>ロボット操作</h1>'
    '<p style="color:#888;font-size:11px;margin:0 0 8px">通常待機中のみ有効</p>'
    '<h2>LED</h2>'
    '<label>左目</label><div class="clr">'
    '<div class="cb" style="background:#f00" data-c="0" onclick="eye(0,this)"></div>'
    '<div class="cb" style="background:#00f" data-c="1" onclick="eye(0,this)"></div>'
    '<div class="cb" style="background:#fc0" data-c="2" onclick="eye(0,this)"></div>'
    '<div class="cb" style="background:#0f0" data-c="3" onclick="eye(0,this)"></div>'
    '<div class="cb" style="background:#f58" data-c="4" onclick="eye(0,this)"></div>'
    '<div class="cb" style="background:#222" data-c="5" onclick="eye(0,this)">✕</div>'
    '</div>'
    '<label>右目</label><div class="clr">'
    '<div class="cb" style="background:#f00" data-c="0" onclick="eye(1,this)"></div>'
    '<div class="cb" style="background:#00f" data-c="1" onclick="eye(1,this)"></div>'
    '<div class="cb" style="background:#fc0" data-c="2" onclick="eye(1,this)"></div>'
    '<div class="cb" style="background:#0f0" data-c="3" onclick="eye(1,this)"></div>'
    '<div class="cb" style="background:#f58" data-c="4" onclick="eye(1,this)"></div>'
    '<div class="cb" style="background:#222" data-c="5" onclick="eye(1,this)">✕</div>'
    '</div>'
    '<h2>サーボ（左翼）</h2>'
    '<label>角度: <span id="sv">90</span>°</label>'
    '<input type="range" id="sa" min="0" max="180" value="90" oninput="setS(this.value)">'
    '<h2>音階</h2>'
    '<div id="pn" style="position:relative;height:80px;margin-top:6px;border:1px solid #444;'
    'border-radius:4px;overflow:hidden;touch-action:manipulation">'
    '<button class="wk" style="left:0"     data-freq="262">ド</button>'
    '<button class="wk" style="left:12.5%" data-freq="294">レ</button>'
    '<button class="wk" style="left:25%"   data-freq="330">ミ</button>'
    '<button class="wk" style="left:37.5%" data-freq="349">ファ</button>'
    '<button class="wk" style="left:50%"   data-freq="392">ソ</button>'
    '<button class="wk" style="left:62.5%" data-freq="440">ラ</button>'
    '<button class="wk" style="left:75%"   data-freq="494">シ</button>'
    '<button class="wk" style="left:87.5%" data-freq="523">ド</button>'
    '<button class="bk" style="left:8.75%"  data-freq="277"></button>'
    '<button class="bk" style="left:21.25%" data-freq="311"></button>'
    '<button class="bk" style="left:46.25%" data-freq="370"></button>'
    '<button class="bk" style="left:58.75%" data-freq="415"></button>'
    '<button class="bk" style="left:71.25%" data-freq="466"></button>'
    '</div>'
    '<h2>なきごえ</h2>'
    '<div class="snd-row">'
    '<button class="sb" onclick="snd(\'call\')">ピッピッピ</button>'
    '<button class="sb" onclick="snd(\'happy\')">ピピピッ↑</button>'
    '<button class="sb" onclick="snd(\'chirp\')">ふわ↑↓</button>'
    '</div>'
    '<a href="/">戻る</a>'
    '<script>'
    'var C=[[255,0,0],[0,0,255],[255,200,0],[0,255,0],[255,80,120],[0,0,0]];'
    'var SD=["l","r"];'
    'function rq(u){var x=new XMLHttpRequest();x.open("GET",u);x.send()}'
    'function eye(s,b){'
    'var c=C[+b.dataset.c];'
    'b.parentNode.querySelectorAll(".cb").forEach(function(e){e.classList.remove("act")});'
    'b.classList.add("act");'
    'rq("/set-eye?side="+SD[s]+"&c="+c.join(","))}'
    'var st;'
    'function setS(v){document.getElementById("sv").textContent=v;'
    'clearTimeout(st);st=setTimeout(function(){rq("/set-servo?angle="+v)},100)}'
    'var pk=null;'
    'function noteOn(f,k){if(pk)pk.classList.remove("p");pk=k;k.classList.add("p");rq("/note-on?freq="+f)}'
    'function noteOff(){if(pk){pk.classList.remove("p");pk=null;}rq("/note-off")}'
    'var pn=document.getElementById("pn");'
    'pn.addEventListener("mousedown",function(e){var f=e.target.dataset.freq;if(f)noteOn(+f,e.target)});'
    'pn.addEventListener("touchstart",function(e){var f=e.target.dataset.freq;if(f){e.preventDefault();noteOn(+f,e.target)}},{passive:false});'
    'document.addEventListener("mouseup",noteOff);'
    'document.addEventListener("touchend",noteOff);'
    'document.addEventListener("touchcancel",noteOff);'
    'function snd(n){rq("/play-sound?name="+n)}'
    '</script></body></html>'
).encode('utf-8')


# ==============================================================
# 【6-3】 SSE（リアルタイムログ配信）
# ==============================================================

async def _handle_sse(writer):
    """SSE（Server-Sent Events）でログをリアルタイムにブラウザへ送り続ける。
    15秒ごとにピングを送ってモバイルで接続が切れないようにしている。"""
    try:
        writer.write(
            b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'
            b'Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n'
        )
        await writer.drain()
        n         = len(_log_lines)
        last_ping = time.time()
        while True:
            await asyncio.sleep(0.1)
            while n < len(_log_lines):
                writer.write(('data: ' + _log_lines[n] + '\n\n').encode('utf-8'))
                n += 1
            if time.time() - last_ping >= 15:
                writer.write(b': ping\n\n')
                last_ping = time.time()
            await writer.drain()
    except Exception:
        pass


# ==============================================================
# 【6-4】 HTTP リクエストの処理
# ==============================================================
# ブラウザから届いたリクエストを判別して、対応するページや処理を返す。

async def handle_http(reader, writer):
    global NORMAL_COLOR, color_edit, VOLUME, last_interaction
    try:
        req   = await reader.read(512)
        parts = req.decode('utf-8', 'ignore').split(' ')
        path  = parts[1] if len(parts) > 1 else '/'

        # キャプティブポータル（Android が WiFi 接続確認で叩くURL）
        # 204 を返すと Android がインターネットありと誤解して大量通信するため 302 にする
        if ('generate_204' in path or 'gen_204'     in path or
                'hotspot'  in path or 'connecttest' in path or 'ncsi' in path):
            writer.write(b'HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\nConnection: close\r\n\r\n')

        elif path == '/' or path.startswith('/?'):
            writer.write(_HOME_HTML)

        elif path == '/logs' or path.startswith('/logs?'):
            writer.write(_LOG_PAGE)

        elif path == '/sse':
            await _handle_sse(writer)
            return

        elif path == '/color' or path.startswith('/color?'):
            r2, g2, b2 = NORMAL_COLOR
            html = _COLOR_TMPL % (r2, r2, g2, g2, b2, b2)
            writer.write(
                ('HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
                 'Cache-Control: no-store\r\nConnection: close\r\n\r\n' + html).encode('utf-8')
            )

        elif path.startswith('/set-color'):
            r2 = g2 = b2 = 0
            save = False
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if   k == 'r':    r2   = max(0, min(255, int(val)))
                        elif k == 'g':    g2   = max(0, min(255, int(val)))
                        elif k == 'b':    b2   = max(0, min(255, int(val)))
                        elif k == 'save': save = True
            except:
                pass
            set_eyes(r2, g2, b2)
            if save:
                NORMAL_COLOR = (r2, g2, b2)
                color_edit   = list(NORMAL_COLOR)
                save_settings()
                log(f"[WiFi] 色保存: RGB({r2},{g2},{b2})")
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nOK')

        elif path == '/volume' or path.startswith('/volume?'):
            acts = [' act' if VOLUME == v else '' for v in [33, 67, 100, 0]]
            html = _VOLUME_TMPL % tuple(acts)
            writer.write(
                ('HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
                 'Cache-Control: no-store\r\nConnection: close\r\n\r\n' + html).encode('utf-8')
            )

        elif path.startswith('/set-volume'):
            v = VOLUME
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if k == 'v': v = max(0, min(100, int(val)))
            except:
                pass
            VOLUME = min(VOLUME_LEVELS, key=lambda x: abs(x - v))
            save_settings()
            log(f"[WiFi] 音量保存: {VOLUME_NAMES[VOLUME_LEVELS.index(VOLUME)]}")
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nOK')

        elif path == '/ctrl' or path.startswith('/ctrl?'):
            writer.write(_CTRL_PAGE)

        elif path.startswith('/set-eye'):
            side = 'both'
            r2 = g2 = b2 = 0
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if k == 'side': side = val
                        elif k == 'c':
                            p2 = val.split(',')
                            r2, g2, b2 = int(p2[0]), int(p2[1]), int(p2[2])
            except:
                pass
            if not busy:
                last_interaction = time.time()
                if   side == 'l': set_eye_l(r2, g2, b2)
                elif side == 'r': set_eye_r(r2, g2, b2)
                else:             set_eyes(r2, g2, b2)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

        elif path.startswith('/set-servo'):
            angle = 90
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if k == 'angle': angle = max(0, min(180, int(val)))
            except:
                pass
            if not busy:
                last_interaction = time.time()
                asyncio.create_task(move_servo(angle, step=5))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

        elif path.startswith('/play-sound'):
            name = ''
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if k == 'name': name = val
            except:
                pass
            if   name == 'call':  asyncio.create_task(bird_call())
            elif name == 'happy': asyncio.create_task(happy_chirp())
            elif name == 'chirp': asyncio.create_task(chirp())
            log(f"[WiFi] なきごえ: {name}")
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\n\r\nOK')

        elif path.startswith('/note-on'):
            freq = 440
            try:
                qs = path.split('?')[1] if '?' in path else ''
                for kv in qs.split('&'):
                    if '=' in kv:
                        k, val = kv.split('=', 1)
                        if k == 'freq': freq = max(100, min(5000, int(val)))
            except:
                pass
            buzzer.freq(freq)
            buzzer.duty_u16(_buzz_duty())
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

        elif path.startswith('/note-off'):
            buzzer.duty_u16(0)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

        else:
            writer.write(_HOME_HTML)

        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ==============================================================
# 【7】 起動処理（ここからプログラムが始まる）
# ==============================================================

async def main():
    global _game_touch, NORMAL_COLOR, color_edit, VOLUME

    # asyncio.Event() はタスク間の「合図」に使う（ゲームのタップ検知）
    # モジュール直下では作れないので、ここで初期化する
    _game_touch = asyncio.Event()

    # 保存された設定（色・音量）を読み込む
    NORMAL_COLOR, VOLUME = load_settings()
    color_edit           = list(NORMAL_COLOR)

    # --- WiFi の設定 ---
    # つまみを左端（設定ゾーン, 0〜25%）にした状態で起動 → WiFi オフ
    wifi_off = pot.read_u16() < 16384

    if wifi_off:
        network.WLAN(network.AP_IF).active(False)   # アクセスポイントを無効化
        network.WLAN(network.STA_IF).active(False)  # ステーション（接続元）も無効化
    else:
        setup_wifi_ap()
        await asyncio.start_server(handle_http, '0.0.0.0', 80)

    log(f"[起動] NORMAL_COLOR: RGB{NORMAL_COLOR}  音量: {VOLUME}%")
    log(f"[起動] WiFi: {'オフ（設定ゾーン起動）' if wifi_off else 'オン'}")

    # --- 起動アニメーション ---
    await servo_neutral()
    eyes_off()
    log("=== シマエナガ 起動! ===")
    if wifi_off:
        # WiFi オフ起動: 違う音 + 青いゆらぎ + 羽パタパタ
        await asyncio.gather(wifi_off_jingle(), led_lonely(), wing_flap(2))
    else:
        # 通常起動: ジングル + ピンク + 羽パタパタ + 鳴き声
        await asyncio.gather(startup_jingle(), led_pink_sparkle(), wing_flap(2))
        await bird_call()
    set_eyes(*NORMAL_COLOR)

    # --- バックグラウンドタスクを起動 ---
    # create_task() で起動した処理は「ながら実行」になる。
    # 以下の3つは main() が終わっても動き続ける。
    asyncio.create_task(touch_task())        # タッチセンサーの監視
    asyncio.create_task(pot_monitor_task())  # つまみの監視・LED 更新
    asyncio.create_task(idle_task())         # 放置検知・気まぐれ動作

    # メインループ（ここで待機しながら上の3タスクが動き続ける）
    while True:
        await asyncio.sleep(3600)


# プログラムを起動する
asyncio.run(main())
