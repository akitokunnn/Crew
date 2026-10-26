"""Data-only CLI. None of these commands creates a model turn."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path

from . import Repository
from . import legacy, packages


def main(argv=None):
    parser = argparse.ArgumentParser(description='Crew workspace history, import/export and portable handoff (no AI calls)')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('CREW_HOME', Path.home()/'.crew')))
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('list')
    migrate=commands.add_parser('migrate');migrate.add_argument('--source',type=Path,required=True);migrate.add_argument('--project',required=True)
    export=commands.add_parser('export');export.add_argument('workspace_id');export.add_argument('destination',type=Path)
    preview=commands.add_parser('preview');preview.add_argument('package',type=Path)
    incoming=commands.add_parser('import');incoming.add_argument('package',type=Path);incoming.add_argument('--mode',choices=('fork','restore_readonly'),default='restore_readonly');incoming.add_argument('--operation-id')
    context=commands.add_parser('context');context.add_argument('workspace_id');context.add_argument('--query');context.add_argument('--goal-id');context.add_argument('--limit-chars',type=int,default=6000)
    handoff=commands.add_parser('handoff');handoff.add_argument('workspace_id');handoff.add_argument('destination',type=Path);handoff.add_argument('--goal-id')
    args=parser.parse_args(argv)
    if args.command=='preview':
        result=packages.preview_package(args.package)
    else:
        repo=Repository(args.home)
        if args.command=='list':result={'workspaces':repo.list_workspaces()}
        elif args.command=='migrate':result=legacy.migrate_workspace(repo,args.source,args.project)
        elif args.command=='export':
            with closing(repo.connect()) as conn:result=packages.export_workspace(conn,repo.objects,args.workspace_id,args.destination)
        elif args.command=='import':
            account,user=repo.ensure_local_identity()
            with closing(repo.connect()) as conn:result=packages.import_package(conn,repo.objects,args.package,mode=args.mode,target_account_id=account,actor_user_id=user,device_id=repo.device_id,operation_id=args.operation_id)
        elif args.command=='context':
            from .context import retrieve
            result=retrieve(repo,args.workspace_id,query=args.query,goal_id=args.goal_id,limit_chars=args.limit_chars)
        else:
            from .context import export_handoff
            result=export_handoff(repo,args.workspace_id,args.destination,goal_id=args.goal_id)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
