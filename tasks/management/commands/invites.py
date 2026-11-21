from django.core.management.base import BaseCommand
from django.utils import timezone

from tasks import invites
from tasks.models import Invite


class Command(BaseCommand):
    help = "Make personal invite links (<base>/<code>), list them, or revoke them all."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10, help="how many links (default 10)")
        parser.add_argument("--hours", type=float, default=24, help="valid for this long (default 24)")
        parser.add_argument("--base", default="http://127.0.0.1:8000", help="public server address, e.g. the tunnel URL")
        parser.add_argument("--note", default="", help="a note for yourself, e.g. 'для тітки'")
        parser.add_argument("--list", action="store_true", help="show existing links")
        parser.add_argument("--revoke-all", action="store_true", help="kill every link right now")

    def handle(self, count, hours, base, note, list, revoke_all, **_):
        base = base.rstrip("/")
        now = timezone.now()
        if revoke_all:
            n = Invite.objects.filter(expires_at__gt=now).update(expires_at=now)
            self.stdout.write(f"Відкликано: {n}")
            return
        if list:
            for i in Invite.objects.all():
                state = "прострочене" if i.expires_at <= now else f"до {timezone.localtime(i.expires_at):%d.%m %H:%M}"
                used = f"пристроїв: {len(i.sessions)}" if i.sessions else "ще не відкривали"
                self.stdout.write(f"{base}/{i.code}  {state}, {used}  {i.note}")
            return
        made = invites.create(count, hours, note)
        until = timezone.localtime(made[0].expires_at) if made else None
        self.stdout.write(f"{len(made)} запрошень, дійсні до {until:%d.%m %H:%M}:")
        for i in made:
            self.stdout.write(f"{base}/{i.code}")
