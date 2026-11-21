export const reconcileViewTemplate = `
  <div v-show="tab===4" class="panel">
    <h2>对账互证</h2>
    <p :class="recon.all_ok ? 'ok' : 'err'">{{ recon.all_ok ? '全部同源' : '存在分裂' }}</p>
    <table>
      <thead><tr><th>档</th><th>盘面已订</th><th>正式册席</th><th>占位中</th><th>可占席</th><th>容量核</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="i in (recon.items||[])" :key="i.slot_code">
          <td>{{ i.slot_code }}</td><td>{{ i.slot_booked }}</td>
          <td>{{ i.booking_seats }}</td><td>{{ i.pending_seats }}</td>
          <td>{{ i.sold_out ? '售罄' : i.free_seats }}</td>
          <td :class="i.capacity_ok?'ok':'err'">{{ i.capacity_ok ? '未超' : '超卖' }}</td>
          <td :class="i.light_ok?'ok':'err'">{{ i.light_ok ? '通过' : '分裂' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
