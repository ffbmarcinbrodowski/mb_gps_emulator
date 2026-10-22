"""
IMEI generator for new emulated devices.

An IMEI has 15 digits: 8-digit TAC (type allocation code) + 6-digit serial number
+ 1 Luhn check digit. Generated IMEIs start with "00": reporting body 00 is reserved
for test IMEIs (GSMA TS.06), so they never collide with a real tracker - important
when sending to a shared server such as the Traccar demo.
"""
import random

TEST_PREFIX = "00"


def luhn_check_digit(digits):
    """Check digit for a string of digits (the same algorithm as credit card numbers)."""
    total = 0
    # Double every second digit, starting from the rightmost one.
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def is_valid_imei(imei):
    return len(imei) == 15 and imei.isdigit() and luhn_check_digit(imei[:14]) == imei[14]


def generate_imei(taken=()):
    """Random valid test IMEI that is not in `taken`."""
    while True:
        body = TEST_PREFIX + "".join(random.choice("0123456789") for _ in range(12))
        imei = body + luhn_check_digit(body)
        if imei not in taken:
            return imei
