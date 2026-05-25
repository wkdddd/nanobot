class Student:
    # 类属性
    school = "第一中学"

    # 实例初始化
    def __init__(self, name, age):
        self.name = name  # 实例属性
        self.age = age

    # 1. 普通实例方法：必须传self，操作实例自身数据
    def show_info(self):
        print(f"姓名：{self.name}，年龄：{self.age}，学校：{self.school}")

    # 2. 类方法：@classmethod，固定传cls，代表当前类
    @classmethod
    def change_school(cls, new_school):
        cls.school = new_school  # 修改类属性
        print(f"统一修改学校为：{cls.school}")

    # 3. 静态方法：@staticmethod，无默认参数，纯独立工具
    @staticmethod
    def is_adult(age):
        # 只靠传入参数运算，不碰类、实例任何属性
        return age >= 18


# ========== 调用演示 ==========
# 1. 实例方法：必须先创建对象
stu1 = Student("张三", 17)
stu1.show_info()

# 2. 类方法：类直接调用，也可对象调用
Student.change_school("实验中学")
stu1.show_info()

# 3. 静态方法：类/对象都能调用，不用实例属性
print(Student.is_adult(17))
print(stu1.is_adult(19))