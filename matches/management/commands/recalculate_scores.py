from django.core.management.base import BaseCommand
from matches.models import Round
from matches.services.scoring import recalculate_round_scores
class Command(BaseCommand):
    def add_arguments(self,parser): parser.add_argument('--round',type=int)
    def handle(self,*args,**opts):
        rounds=Round.objects.filter(pk=opts['round']) if opts['round'] else Round.objects.all()
        for round_ in rounds: self.stdout.write(f'{round_}: {len(recalculate_round_scores(round_))} users recalculated')
