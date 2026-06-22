<?php
/* catalogo.php · lee la vista vw_catalogo_productos */
$titulo = 'Catálogo';
$activo = 'catalogo';
require __DIR__ . '/includes/header.php';

// --- filtros ---
$q       = trim($_GET['q'] ?? '');
$filtro  = $_GET['estatus'] ?? '';   // '', OK, ERROR
$pagina  = max(1, (int)($_GET['p'] ?? 1));
$por_pag = 50;
$offset  = ($pagina - 1) * $por_pag;

$where = [];
$tipos = '';
$args  = [];
if ($q !== '') {
    $where[] = '(Nombre LIKE ? OR Codigo LIKE ? OR CodeProdServ LIKE ?)';
    $like = "%$q%";
    $tipos .= 'sss';
    array_push($args, $like, $like, $like);
}
if ($filtro === 'OK') {
    $where[] = "Errores = 'OK'";
} elseif ($filtro === 'ERROR') {
    $where[] = "Errores <> 'OK'";
}
$sql_where = $where ? ('WHERE ' . implode(' AND ', $where)) : '';

// --- total ---
$sql_count = "SELECT COUNT(*) n FROM vw_catalogo_productos $sql_where";
$stmt = $conn->prepare($sql_count);
if ($args) { $stmt->bind_param($tipos, ...$args); }
$stmt->execute();
$total = (int)$stmt->get_result()->fetch_assoc()['n'];
$paginas = max(1, (int)ceil($total / $por_pag));

// --- datos ---
$sql = "SELECT Codigo, Nombre, Unidad, Precio, UnitCode, CodeProdServ,
               ObjetoImp, PrecioNeto, Errores
        FROM vw_catalogo_productos
        $sql_where
        ORDER BY Nombre
        LIMIT ? OFFSET ?";
$stmt = $conn->prepare($sql);
$tipos2 = $tipos . 'ii';
$args2  = array_merge($args, [$por_pag, $offset]);
$stmt->bind_param($tipos2, ...$args2);
$stmt->execute();
$rows = $stmt->get_result();

// helper para conservar filtros en links
$qs = fn(array $extra) => http_build_query(array_merge(
    ['q'=>$q, 'estatus'=>$filtro, 'p'=>$pagina], $extra));
?>
<h1>Catálogo de productos</h1>
<p class="muted">Vista en vivo (maestro + precio vigente). <?= number_format($total) ?> resultados.</p>

<form class="filtros" method="get">
  <input type="search" name="q" value="<?= h($q) ?>" placeholder="Buscar nombre, código o clave SAT…">
  <select name="estatus">
    <option value=""      <?= $filtro===''?'selected':'' ?>>Todos</option>
    <option value="OK"    <?= $filtro==='OK'?'selected':'' ?>>Listos (OK)</option>
    <option value="ERROR" <?= $filtro==='ERROR'?'selected':'' ?>>Con error</option>
  </select>
  <button type="submit">Filtrar</button>
</form>

<div class="tabla-wrap">
<table class="tabla">
  <thead>
    <tr>
      <th>Código</th><th>Nombre</th><th>Unidad</th>
      <th class="num">Precio</th><th>UnitCode</th><th>Clave SAT</th>
      <th>ObjImp</th><th class="num">Precio neto</th><th>Estado</th>
      <?php if (puede_editar()): ?><th></th><?php endif; ?>
    </tr>
  </thead>
  <tbody>
  <?php if ($rows->num_rows === 0): ?>
    <tr><td colspan="10" class="vacio">Sin resultados. Ajusta la búsqueda.</td></tr>
  <?php else: while ($f = $rows->fetch_assoc()): ?>
    <tr>
      <td class="mono"><?= h($f['Codigo']) ?></td>
      <td><?= h($f['Nombre']) ?></td>
      <td><?= h($f['Unidad']) ?></td>
      <td class="num"><?= mxn($f['Precio']!==null?(float)$f['Precio']:null) ?></td>
      <td class="mono"><?= h($f['UnitCode']) ?: '—' ?></td>
      <td class="mono"><?= h($f['CodeProdServ']) ?: '—' ?></td>
      <td class="mono"><?= h($f['ObjetoImp']) ?: '—' ?></td>
      <td class="num"><?= mxn($f['PrecioNeto']!==null?(float)$f['PrecioNeto']:null) ?></td>
      <td><?= badge_error($f['Errores']) ?></td>
      <?php if (puede_editar()): ?>
        <td><a class="mini" href="editar.php?id=<?= urlencode($f['Codigo']) ?>">Editar</a></td>
      <?php endif; ?>
    </tr>
  <?php endwhile; endif; ?>
  </tbody>
</table>
</div>

<?php if ($paginas > 1): ?>
<nav class="paginacion">
  <?php if ($pagina > 1): ?>
    <a href="?<?= $qs(['p'=>$pagina-1]) ?>">‹ Anterior</a>
  <?php endif; ?>
  <span>Página <?= $pagina ?> de <?= $paginas ?></span>
  <?php if ($pagina < $paginas): ?>
    <a href="?<?= $qs(['p'=>$pagina+1]) ?>">Siguiente ›</a>
  <?php endif; ?>
</nav>
<?php endif; ?>

<?php require __DIR__ . '/includes/footer.php'; ?>
