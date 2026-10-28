"""Names for newly opened discussions, before the leader knows their subject."""


def initial_name(conn, project):
    occupied = {row[0] for row in conn.execute(
        'SELECT crew_name FROM sessions WHERE project=?', (project,))}
    number = 1
    while True:
        candidate = '新对话' if number == 1 else '新对话 ' + str(number)
        if candidate not in occupied:
            return candidate
        number += 1
