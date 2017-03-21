#!/usr/bin/evn python
# -*- coding:utf-8 -*-

__author__ = 'liuyizhen'

'''
选择mysql作为网站后台数据库
执行sql语句进行操作，并将常用的select、insert、update等语句进行函数封装
在异步框架（aiohttp）的基础上，采用aiomysql作为数据库的异步io驱动
在数据库中表的操作，映射成一个类的操作，也就是数据库表的一行映射成一个队员（orm）
整个orm也是异步操作
一层异步，层层异步

预备知识：python协程、异步io（yield from）、sql数据库操作、元类、面向对象知识、python语法

思路：
    如何定义一个user类，这个类和数据库中的表user构成映射关系，二者关联起来，user类可以操作user表
    通过Field类将user类的属性映射到user表的列中，其中每一列的字段又有自己的一些属性，包括数据类型、列名、主键和默认值

tips：
    1、调用user.save()并没有效果，因为调用save()仅仅是创建了一个协程，并未执行它。 需要调用 yield from user.save()才是执行了insert操作
'''

import asyncio, logging
import aiohttp, aiomysql, sys


# 打印sql查询语句
def log(sql, args=()):
    logging.info('SQL: %s' % sql)


# 创建一个全局的连接池，每个http请求都从连接池中获取数据库连接。 **kw参数可以包含所有连接需要用到的关键字参数 "key"="value"
async def create_pool(loop, **kw):
    logging.info('create database connection pool...')

    # 全局变量__pool用于存储整个连接池
    global __pool

    # dict有一个get方法，如果dict中有对应的value值，则返回对应于key的value值，否则返回默认值，即host中的'localhost'
    __pool = await aiomysql.create_pool(
        # 默认本机ip
        host=kw.get('host', 'localhost'),
        user=kw['user'],
        passowrd=kw['password'],
        db=kw['db'],
        port=kw.get('port', 3306),
        charset=kw.get('charset', 'utf8'),
        autocommit=kw.get('autocommit', True),
        # 默认最大连接数为10，最小为1
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        # 接收一个event_loop实例
        loop=loop
    )


async def destroy_pool():
    global __pool
    if __pool is not None:
        __pool.close()  # 这个方法不是一个协程，因此不用await或yield from。 aiomysql模块中哪些是协程，哪些不是，可参照http://aiomysql.readthedocs.io/en/latest/pool.html
        await  __pool.wait_closed()


# 封装sql select语句为select函数
async def select(sql, args, size=None):
    log(sql, args)
    global __pool

    # yield from (await)将调用一个子协程，并直接返回调用的结果
    # 从连接池中返回一个连接
    async with __pool.get() as conn:
        # DictCurosr is a cursor which returns results as a dictionary
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 执行sql语句，sql语句占位符为？，mysql占位符为%s
            await cur.execute(sql.replace('?', '%s'), args or ())
            # 返回全部数据或指定数据量
            if size:
                res = await cur.fetchmany(size)
            else:
                res = await  cur.fetchall()

            logging.info('rows returned: %s' % len(res))
            return res


# 封装insert、update、delete
# 语句操作参数一样，所以定义一个通用的执行函数
# 返回操作影响的行数
async def execute(sql, args, autocommit=True):
    log(sql, args)

    async with __pool.get() as conn:
        if not autocommit:
            await conn.begin()

        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                affected = cur.rowcount
            if not autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                await conn.rollback()
            raise

        return affected


# 根据输入的参数生成占位符列表
# 将查询字段计数替换成sql识别的?
# 如： insert into `user`(`password`,`email`,`name`,`id`) values(?,?,?,?)
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    # 以‘，’为分隔符，将列表合成字符串
    return (','.join(L))


# 定义Field类，负责保存（数据库）表的字段名和字段类型
class Field(object):
    # 表的字段包含名字、类型、是否为表的主键和默认值
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    # 当打印（数据库）表时，数据（数据库）表的信息：类名，字段类型和名字
    def __str__(self):
        return ('<%s, %s:%s>' % (self.__class__, self.column_type, self.name))


# 定义不同类型的衍生Field
# 表的不同列的字段的类型不一样
class StringField(Field):
    def __init__(self, name=None, primary_key=False, default=None, column_type='varchar(100)'):
        super().__init__(name, column_type, primary_key, default)


class BooleanField(Field):
    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)


class IntegerField(Field):
    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)


class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)


class TextField(Field):
    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


# 定义Model的元类

# 所有的元类都继承自type（元类知识另行补充）
# ModelMetaClass元类定义了所有Model基类（继承ModelMetaClass）的子类实现的操作

# ModelMetaClass的工作主要是为一个数据库表映射成一个封装的类做准备：读取子类（user）的映射信息
# 创造类的时候，排除对Model类的修改
# 在当前类中查找所有的类属性（attrs），如果找到Field属性，就将其保存到__mapping__的dict中，同时从类属性中删除Field（防止实例属性遮住类的同名属性）
# 将数据库表名保存到__table__中

# 完成这些工作就可以在Model中定义各个数据库的操作方法
class ModelMetaClass(type):
    # __new__控制__init__的执行，所以在其执行之前
    # cls：要__init__的类，此参数在实例化时，由python解释器自动提供（例如后面的User和Model）
    # bases：继承父类的集合
    # attrs：类的方法集合
    def __new__(cls, name, bases, attrs):
        # 排除Model
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)

        # 获取table名称
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s(table: %s' % (name, tableName))

        # 获取Field和主键名
        mappings = dict()
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            # Field属性
            if isinstance(v, Field):
                # 此处打印的k是类的一个属性，v是这个属性在数据库中对应的Field列表属性
                logging.info(' found mapping: %s==>%s' % (k, v))
                mappings[k] = v

                # 找到主键
                if v.primary_key:
                    # 如果此时类实例已经存在主键，则说明主键重复了
                    if primaryKey:
                        raise BaseException('Duplicate primary key for field: %s' % k)
                    # 否则将此列设为列表的主键
                    primaryKey = k
                else:
                    fields.append(k)
        # end for


        if not primaryKey:
            raise BaseException('Primary key is not found')

        # 从类属性中删除Field属性
        for k in mappings.keys():
            attrs.pop(k)

        # 保存除主键外的属性,名为``（运算出字符串）列表形式
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))

        # 保存属性和列的映射关系
        attrs['__mappings__'] = mappings
        # 保存表名
        attrs['__table__'] = tableName
        # 保存主键属性名
        attrs['__primary_key__'] = primaryKey
        # 保存除主键外的属性名
        attrs['__fields__'] = fields

        # 构造默认的select、insert、update、delete语句
        # ``反引号功能同repr()
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ','.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values(%s)' % (
            tableName, ','.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s` = >' % (
            tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s` = ?' % (tableName, primaryKey)

        return type.__new__(cls, name, bases, attrs)


# 定义orm所有映射的基类：Model
# Model类的任意子类可以映射一个数据库表
# Model类可以看做是对所有数据库表操作的基本定义的映射

# 基于字段查询形式
# Model从dict继承，拥有字典的所有功能，懂事实现特殊方法__getattr__和__setattr__，能够实现属性操作
# 实现数据库操作的所有方法，定义为class方法（类方法），所有继承自Model都具有数据库操作方法
class Model(dict, metaclass=ModelMetaClass):
    def __index__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r'"Model" object has no attribute: %s' % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        # 内建函数getattr会自动处理
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if not value:
            field = self.__mappings__[key]
            if filter.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s:%s' % (key, str(value)))
                setattr(self, key, value)
        return value

    # 类方法有类变量cls传入，从而可以用cls做一些相关的处理。 并且有子类继承时，调用该类方法时，传入的类变量cls是子类，而非父类
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        '''find objects by where clause'''
        sql = [cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []

        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)

        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        res = await select(' '.join(sql), args)
        return [cls(**r) for r in res]

    @classmethod
    async def findNumber(cls, selectField, where=None, args=None):
        '''find number by select and where'''
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        res = await select(' '.join(sql), args, 1)
        if len(res) == 0:
            return None
        return res[0]['_num_']

    @classmethod
    async def find(cls, pk):
        '''find object by primary key'''
        res = await select('%s where `%s` = ?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(res) == 0:
            return None
        return cls(**res[0])

    async def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warning('failed to insert record: affected rows: %s' % rows)

    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warning('failed to update by primary key: affected rows: %s' % rows)

    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warning('failed to remove by primary key: affected rows: %s' % rows)


if __name__ == '__main__':  # 一个类自带前后都有双下划线的方法， 在子类继承该类的时候，这些方法会自动调用，比如__init__
    class User(Model):  # 虽然User类乍看之下没有参数传入，但实际上，User类继承自Model类，Model类又继承自dict类，所以User类的实例可以传入关键字参数**kw
        id = IntegerField('id', primary_key=True)
        name = StringField('name')

        # 创建异步事件的句柄
        loop = asyncio.get_event_loop()

        # 创建实例
        async def test(self):
            await create_pool(loop=loop, host='localhost', port=3306, user='root', password='qbslyz04067891.',
                              db='test')
            r = await User.findAll()
            print(r)
            await destroy_pool()

        loop.run_until_complete(test())
        loop.close()
        if loop.is_closed():
            sys.exit(0)
