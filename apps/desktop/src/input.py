"""One bounded input operation. No shell commands, eval, or arbitrary script execution."""
import json
import sys
import signal

import pyautogui

pyautogui.FAILSAFE = True  # moving the pointer to a screen corner also aborts input
pyautogui.PAUSE = 0.005
def stop(_signal, _frame):
    raise KeyboardInterrupt('Computer support stopped')

signal.signal(signal.SIGTERM, stop)
request = json.load(sys.stdin)
action = request['action']
if action == 'click':
    bounds = request['bounds']
    x = bounds['x'] + round(request['x'] * (bounds['width'] - 1))
    y = bounds['y'] + round(request['y'] * (bounds['height'] - 1))
    pyautogui.click(x, y, button=request.get('button', 'left'))
elif action == 'type':
    # A single character at a time keeps failsafe checks active during long input.
    for char in request['text']:
        if char in ('\n', '\t'):
            pyautogui.press('enter' if char == '\n' else 'tab')
        else:
            pyautogui.write(char)
elif action == 'key':
    try:
        pyautogui.hotkey(*request['keys'])
    finally:
        # Release modifiers even if a stop/failsafe interrupts a chord.
        pyautogui.FAILSAFE = False
        for key in reversed(request['keys']):
            pyautogui.keyUp(key, _pause=False)
elif action == 'scroll':
    pyautogui.scroll(request['amount'])
else:
    raise ValueError('Unsupported input action')
print(json.dumps({'ok': True}))
