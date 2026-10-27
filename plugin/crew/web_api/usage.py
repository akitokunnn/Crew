"""Usage HTTP interface; dependencies are supplied explicitly at composition."""
import csv
import io
from .. import usage_cli
from .registry import Content


def register(routes, db, usage):
    def get_query(request):
        query = request.query
        period = query.get('period', ['week'])[0]
        if request.path.endswith('/export'):
            what = query.get('what', [None])[0]
            if what not in usage_cli.EXPORT_FIELDS:
                raise ValueError('导出类型必须是 sessions / daily / codex / requests')
            rows = usage(period, what, query.get('project', [None])[0], query.get('scope', ['workspace'])[0])
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=usage_cli.EXPORT_FIELDS[what])
            writer.writeheader()
            writer.writerows(({key: '' if value is None else usage_cli._format(value, key) for key, value in row.items()} for row in rows))
            return Content(stream.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8', 'crew-' + what + '.csv')
        else:
            return usage(period, project_name=query.get('project', [None])[0], scope=query.get('scope', ['workspace'])[0])

    routes.add('GET', ['/api/usage', '/api/usage/export'], get_query)
