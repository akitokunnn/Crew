"""Project palette catalogue shared by persisted choices and the web UI."""

BACKGROUND_TOKENS = ('--bg', '--panel', '--panel-2', '--surface', '--surface-2', '--user-bubble')


def _palette(accent, strong, backgrounds, dark):
    rgb = ','.join(str(int(accent[index:index + 2], 16)) for index in (1, 3, 5))
    return {
        '--accent': accent,
        '--accent-strong': strong,
        '--accent-text': accent if dark else strong,
        '--on-accent': '#15201E' if dark else '#F7FBFA',
        '--accent-soft': 'rgba(%s,%s)' % (rgb, '.15' if dark else '.12'),
        '--accent-softer': 'rgba(%s,%s)' % (rgb, '.07' if dark else '.06'),
        **{token: '#' + color for token, color in zip(BACKGROUND_TOKENS, backgrounds.split())},
    }


def _theme(identity, name, light, dark):
    priority = {'--p1': '#008F88' if identity == 'mist' else '#008FA8',
                '--p2': '#687700' if identity == 'lotus' else '#B52B96',
                '--p3': '#687700' if identity == 'indigo' else '#5555DC'}
    return {'id': identity, 'name': name,
            'light': {**_palette(*light, dark=False), **priority},
            'dark': {**_palette(*dark, dark=True), **priority}}


THEMES = [
    _theme('celadon', '青瓷',
           ('#3E7C74', '#2E6760', 'F6F5F1 EFEEE8 F2F1EC FBFAF7 FCFBF8 E9E7E0'),
           ('#7DB3AA', '#9CCAC1', '1E1E1C 191917 1B1B19 252523 272725 2E2E2B')),
    _theme('indigo', '靛蓝',
           ('#536595', '#425580', 'F5F5F8 ECECF2 F0F0F5 FBFBFD FCFCFE E5E6EE'),
           ('#A9B6DC', '#C0CBEB', '1D1F25 181A20 1A1C22 24262D 272931 2C2F39')),
    _theme('wisteria', '紫藤',
           ('#765E91', '#634D7D', 'F7F5F8 EFEBF2 F3EFF5 FCFAFD FDFBFE EAE4EF'),
           ('#BEA8D2', '#D1BCE2', '211E25 1B181F 1E1B22 28252D 2B2731 332D39')),
    _theme('lotus', '藕荷',
           ('#855D79', '#724A66', 'F8F5F7 F0EAEE F4EFF2 FCFAFC FDFBFD ECE4EA'),
           ('#CBA7BF', '#DEBDD1', '241E24 1E181E 211B21 2C252C 2F272F 382E37')),
    _theme('mist', '雾蓝',
           ('#4F6D7C', '#3D5B69', 'F3F6F7 EAF0F2 EEF3F5 FAFCFD FBFDFE E2EBEF'),
           ('#A0BCCB', '#B7D0DA', '1C2124 171C1F 1A1F22 23292D 262C30 2B3338')),
    _theme('graphite', '石墨',
           ('#62665D', '#4F534B', 'F5F5F1 ECEDE7 F0F1EB FAFBF7 FBFCF8 E5E8DE'),
           ('#B9BDB1', '#CFD1C7', '20211E 1A1B18 1D1E1B 272924 2A2C27 30332C')),
]
IDS = tuple(theme['id'] for theme in THEMES)
