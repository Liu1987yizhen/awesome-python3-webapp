#!/usr/bin/evn python
# -*- coding:utf-8 -*-


from orm import StringField, Model, IntegerField




class User(Model):
    __table__ = 'users'

    id = IntegerField(primary_key=True)
    name = StringField


user = User(id=123, name='Micheal')
user.insert()
users = User.findAll()

print(users)
