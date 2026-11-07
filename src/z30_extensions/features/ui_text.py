# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Owned UI labels using the native Chinese language/font configuration."""
import struct
from ..assembly import assemble, constant, invoke
from . import cjk_fonts
from ..platform.fonts import HANS_FONT, HANT_FONT, missing_glyphs

LANGUAGE_KEY = 0x1400
SIMPLIFIED, TRADITIONAL = 26, 27
# Native Unicode setters use style 120. Its default font 15 lacks CJK;
# choose the stock Hans font 2 / Hant font 0 in the runtime style copy.
# Those fonts cover only stock menu characters; cjk_fonts adds the missing
# label characters and ASCII (see platform/fonts.py), and every label is
# checked against the extended fonts.
UNICODE_STYLE = 120
TRANSLATIONS = {
    'Photo LUT': ('照片 LUT', '照片 LUT'),
    'Load SD LUT': ('加载 SD LUT', '載入 SD LUT'),
    'Use embedded LUT': ('使用内置 LUT', '使用內建 LUT'),
    'Disable and release': ('关闭并释放', '關閉並釋放'),
    'Disabled': ('已关闭', '已關閉'),
    'Embedded LUT': ('内置 LUT', '內建 LUT'),
    'SD LUT ready': ('SD LUT 已就绪', 'SD LUT 已就緒'),
    'Invalid LUT': ('无效的 LUT', '無效的 LUT'),
    'LUT too large': ('LUT 文件过大', 'LUT 檔案過大'),
    'Cleanup pending': ('等待释放资源', '等待釋放資源'),
    'Loaded; cleanup pending': ('已加载，等待释放资源', '已載入，等待釋放資源'),
    'File access failed': ('访问文件失败', '存取檔案失敗'),
    'Wi-Fi buffer': ('Wi-Fi \u53d1\u9001', 'Wi-Fi \u50b3\u9001'),
    '64 KiB (stock)': ('64 KiB (stock)', '64 KiB (stock)'),
    'Photo zebra': ('照片高光显示', '照片高光顯示'),
    'Portrait UI': ('竖拍显示', '豎拍顯示'),
    'NEF compression': ('NEF 压缩', 'NEF 壓縮'),
    'Lossless': ('无失真压缩', '無失真壓縮'),
    'Compressed': ('压缩', '壓縮'),
    'Uncompressed': ('未压缩', '未壓縮'),
    'Photo aspect': ('照片画幅', '照片畫幅'),
    'Date stamp': ('日期水印', '日期水印'),
    'Y M D': ('年 月 日', '年 月 日'),
    'Y M D H:M': ('年 月 日 时:分', '年 月 日 時:分'),
    'M D Y': ('月 日 年', '月 日 年'),
    'D M Y': ('日 月 年', '日 月 年'),
    'D H:M': ('日 时:分', '日 時:分'),
    'Color': ('颜色', '色彩'),
    'Orange': ('橙色', '橙色'),
    'Red': ('红色', '紅色'),
    'Yellow': ('黄色', '黃色'),
    'White': ('白色', '白色'),
    'Glow': ('光晕', '光暈'),
    'OFF (stock)': ('关闭', '關閉'),
    'Video bitrate': ('视频码率', '影片位元率'),
    'Stock': ('原厂', '原廠'),
    'Balanced': ('均衡', '均衡'),
    'High quality': ('高质量', '高品質'),
    'OK': ('确定', '確定'),
    'Settings not saved': ('设置保存失败', '設定儲存失敗'),
    'Shutter sound': ('快门声音', '快門聲音'),
    'Extensions': ('自定义菜单', '自訂選單'),
    'Select sound': ('选择声音', '選擇聲音'),
    'Sound enabled': ('声音开关', '聲音開關'),
    'Enabled': ('启用声音', '啟用聲音'),
    'Sound file': ('声音文件', '聲音檔案'),
    'Preview sound': ('播放声音', '播放聲音'),
    'Reload sound': ('重新加载', '重新載入'),
    'OFF': ('关闭', '關閉'), 'ON': ('开启', '開啟'),
    'None': ('未选择', '未選擇'),
    'File not found': ('找不到文件', '找不到檔案'),
    'Read failed': ('读取失败', '讀取失敗'),
    'Invalid WAV': ('无效的 WAV 文件', '無效的 WAV 檔案'),
    'Close failed': ('关闭文件失败', '關閉檔案失敗'),
    'Sound busy': ('声音正在播放', '聲音正在播放'),
    'Play failed': ('播放失败', '播放失敗'),
    'Audio failed': ('音频错误', '音訊錯誤'),
    'SD Modules': ('SD 卡功能', 'SD 卡功能'),
    'Load CORE.ZMOD': ('加载 CORE.ZMOD', '載入 CORE.ZMOD'),
    'Run module': ('运行功能', '執行功能'),
    'State': ('运行状态', '執行狀態'),
    'No module output': ('暂无输出', '尚無輸出'),
    'Not loaded': ('未加载', '未載入'), 'Loading': ('加载中', '載入中'),
    'Loaded': ('已加载', '已載入'), 'Running': ('运行中', '執行中'),
    'Bad module': ('无效的功能', '無效的功能'),
    'No memory': ('内存不足', '記憶體不足'),
    'Cache unavailable': ('缓存不可用', '快取無法使用'),
    'Init failed': ('初始化失败', '初始化失敗'),
    'No handler': ('缺少事件处理函数', '缺少事件處理函式'),
    'Reboot to reload': ('请重启相机以重新加载', '請重啟相機以重新載入'),
    'Run failed': ('运行失败', '執行失敗'),
    'Host busy': ('功能正在运行', '功能正在執行'),
    'Memory retained': ('内存仍被占用', '記憶體仍被占用'),
}


def utf16_label(text):
    raw = text.encode('utf-16-le')
    if '\0' in text or len(raw) > 48:
        raise ValueError('Native UTF-16 UI labels must fit 24 code units without embedded NUL')
    return raw + b'\0\0'


def check_glyphs(english, hans, hant):
    problems = [f'{name} {text!r} lacks {missing}'
                for text, font, name in ((hans, HANS_FONT, 'Simplified'), (hant, HANT_FONT, 'Traditional'))
                if (missing := missing_glyphs(text, font))]
    if problems:
        raise ValueError(f'Label {english!r} needs glyphs the stock fonts lack: ' + '; '.join(problems))


class TextResources:
    def __init__(self, plan):
        self.plan = plan
        self.records = {}

    def alloc(self, english):
        if english not in self.records:
            hans, hant = TRANSLATIONS.get(english, (english, english))
            check_glyphs(english, hans, hant)
            pointers = [self.plan.alloc(english.encode('ascii') + b'\0'),
                        self.plan.alloc(utf16_label(hans)), self.plan.alloc(utf16_label(hant)),
                        self.plan.alloc(utf16_label(english))]
            self.records[english] = self.plan.alloc(struct.pack('<4I', *pointers))
        return self.records[english]


def prepare(plan, functions):
    c = constant
    native = invoke
    cjk_fonts.add(plan, functions)
    slot_base, style_base = plan.image.words(0x104552c4, 2)
    if plan.image.read(style_base + UNICODE_STYLE*16, 4) != bytes.fromhex('0f240264'):
        raise ValueError('Unexpected native Unicode style')
    runtime_style = slot_base + 0xe10 + UNICODE_STYLE*16
    functions['text'] = plan.alloc(bytes(192))
    functions['text_ascii'] = plan.alloc(bytes(224))
    functions['text_bind'] = plan.alloc(bytes(160))
    functions['text_entry'] = functions['text']
    functions['entry_select'] = plan.alloc(bytes(128))
    code = f'''
        push {{r4-r6,lr}}; mov r4,r0; mov r5,r1; movs r6,#2;
        {c('r0',LANGUAGE_KEY)} {native(0x10465984)}
        cmp r0,#{SIMPLIFIED}; beq unicode; cmp r0,#{TRADITIONAL}; bne bind;
        unicode: movs r6,#3;
        bind: ldr r0,[r4,#12]; cmp r0,r6; beq put;
        mov r0,r4; movs r1,#0; movs r2,#0; {native(0x104546c2)}
        put: mov r0,r4; mov r1,r5; mov r2,r6; {native(0x104546c2)}
        pop {{r4-r6,pc}};
    '''
    plan.put(functions['text_bind'], assemble(code, functions['text_bind'], 160))
    code = f'''
        push {{r4-r6,lr}}; mov r4,r0; mov r5,r1; mov r6,r2;
        {c('r0',LANGUAGE_KEY)} {native(0x10465984)}
        cmp r0,#{SIMPLIFIED}; beq hans; cmp r0,#{TRADITIONAL}; beq hant;
        ldr r0,[r4]; mov r1,r5; mov r2,r6; {native(0x10454efe)} b done;
        hans: movs r6,#{HANS_FONT}; ldr r0,[r4,#4]; b chinese;
        hant: movs r6,#{HANT_FONT}; ldr r0,[r4,#8];
        chinese: mov r1,r5; movs r2,#{UNICODE_STYLE}; {native(0x10454f5a)}
        {c('r0',runtime_style)} strb r6,[r0];
        done: pop {{r4-r6,pc}};
    '''
    plan.put(functions['text'], assemble(code, functions['text'], 192))
    # ABI module output remains ASCII. Chinese fonts consume UTF-16, so
    # convert a bounded copy rather than interpreting pairs of ASCII bytes.
    code = f'''
        push {{r4-r6,lr}}; sub sp,#56; mov r4,r0; mov r5,r1; mov r6,r2;
        {c('r0',LANGUAGE_KEY)} {native(0x10465984)}
        cmp r0,#{SIMPLIFIED}; beq hans; cmp r0,#{TRADITIONAL}; beq hant;
        mov r0,r4; mov r1,r5; mov r2,r6; {native(0x10454efe)} b done;
        hans: movs r6,#{HANS_FONT}; b chinese;
        hant: movs r6,#{HANT_FONT};
        chinese: mov r0,sp; movs r2,#24;
        copy: ldrb r1,[r4],#1; strh r1,[r0],#2; cmp r1,#0; beq put;
        subs r2,#1; bne copy; movs r1,#0; strh r1,[r0];
        put: mov r0,sp; mov r1,r5; movs r2,#{UNICODE_STYLE}; {native(0x10454f5a)}
        {c('r0',runtime_style)} strb r6,[r0];
        done: add sp,#56; pop {{r4-r6,pc}};
    '''
    plan.put(functions['text_ascii'], assemble(code, functions['text_ascii'], 224))
    return TextResources(plan)


def configure_entry(plan, functions, ascii_controls, unicode_controls, name='entry_select'):
    """Select matching row metadata before stock setup creates its widgets."""
    c = constant
    code = f'''
        push {{r4,lr}}; mov r4,r0;
        {c('r0',LANGUAGE_KEY)} {invoke(0x10465984)}
        cmp r0,#{SIMPLIFIED}; beq unicode; cmp r0,#{TRADITIONAL}; beq unicode;
        {c('r1',ascii_controls)} b put;
        unicode: {c('r1',unicode_controls)}
        put: ldr r0,[r4,#40]; str r1,[r0,#12]; mov r0,r4;
        pop {{r4,pc}};
    '''
    plan.put(functions[name], assemble(code, functions[name], 128))


def row_code(functions, setup_object, entry_id, owned_pages, native=()):
    """Replace a prefetched setup entry descriptor before native construction.

    Setup's initialization callback can run after the caller has fetched the
    row pointer. Updating metadata alone only fixes subsequent refreshes.
    Replace saved r1 for this call; both descriptor tables remain read-only.
    Direct rows in `native` shooting menus get the same treatment by row number.
    """
    c = constant
    call = lambda name: invoke(functions[name])
    owned = ''.join(f"{c('r4', (page << 16) | 2)} cmp r3,r4; beq labels;"
                    for page in owned_pages)
    menus = ''
    for number, menu in enumerate(native):
        following = f'native{number+1}' if number + 1 < len(native) else 'owned'
        matches = ''.join(f"{c('r4',ident)} cmp r3,r4; bne skip{number}_{row}; movs r4,#{menu['first_row']+row}; b fix{number}; skip{number}_{row}:"
                          for row, ident in enumerate(menu['ids']))
        menus += f'''native{number}: {c('r4',menu['object'])} cmp r0,r4; bne {following};
            ldrh r3,[r1,#24]; {matches} b restore;
            fix{number}: {call(menu['select'])} {call('labels')}
            ldr r0,[sp]; ldr r1,[r0,#40]; ldr r1,[r1,#12];
            movs r3,#28; mla r1,r4,r3,r1; str r1,[sp,#4]; b restore;'''
    first = 'native0' if native else 'owned'
    return f'''
        push {{r0-r4,lr}}; {c('r4',setup_object)} cmp r0,r4; bne {first};
        ldrh r3,[r1,#24]; {c('r4',entry_id)} cmp r3,r4; bne restore;
        {call('entry_select')} {call('labels')}
        ldr r0,[sp]; ldr r1,[r0,#40]; ldr r1,[r1,#12];
        {c('r4',38*28)} add r1,r4; str r1,[sp,#4]; b restore;
        {menus}
        owned: ldr r3,[r0]; {owned} b restore;
        labels: {call('labels')}
        restore: pop {{r0-r4,lr}}; push.w {{r0-r11,lr}};
        mov r7,r0; ldr r0,[r0,#40];
        .balign 4; ldr.w pc,[pc,#0]; .word 0x1045e345;
    '''
