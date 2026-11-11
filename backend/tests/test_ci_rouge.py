# requires : rien.
# assigns  : rien.
# ensures  : échoue toujours, pour vérifier que la CI bloque.
def test_ci_rouge() -> None:
    var: int = 1
    r: int = 2
    assert var == r
