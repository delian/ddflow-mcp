def parse(text):
    return [line.rstrip('\n').split(',') for line in text.splitlines()]
