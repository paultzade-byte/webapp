import json

from channels.generic.websocket import AsyncWebsocketConsumer


class GameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("game_group", self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard("game_group", self.channel_name)

    # this method is called when the job is done group_send from the type="counter.update"
    async def counter_update(self, event):
        await self.send(text_data=json.dumps({"value": event["value"]}))
