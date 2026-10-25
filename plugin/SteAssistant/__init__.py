# -*- coding: utf-8 -*-
"""STE Assistant — плагин STE Slicer с чат-ассистентом на LLM."""


def getMetaData():
    return {}


def register(app):
    from . import SteAssistant
    return {"extension": SteAssistant.SteAssistantExtension()}
