def my_gen():
    yield 1
    yield 2
    yield 3
g = my_gen()
print(next(g))  # 1
print(next(g))  # 2
print(next(g))  # 3