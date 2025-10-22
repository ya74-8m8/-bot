import discord
from discord import app_commands
from discord.ext import commands
import datetime
import asyncio
import re
import os
import psycopg2 
from urllib.parse import urlparse

TOKEN = "MTQyMjE4NzIxMjE5MTE3NDc0Nw.GypbSZ.HyAdJrj7jEzdN0zfgQbwOI5h_Ee-S66eKEJfyQ"
LOG_CHANNEL_ID = 1420051484929687686
MUTE_ROLE_ID = 1426205474071511040
DATABASE_URL = os.environ.get("DATABASE_URL")

def parse_duration(duration_str: str) -> int | None:
    match = re.match(r'(\d+)([dhms])', duration_str.lower())
    if not match:
        return None
    
    amount = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'd':
        return amount * 24 * 60
    elif unit == 'h':
        return amount * 60
    elif unit == 'm':
        return amount
    elif unit == 's':
        return amount / 60
    return None

def format_duration(minutes: int) -> str:
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440}日間"
    elif minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}時間"
    elif minutes >= 1:
        return f"{minutes}分間"
    return f"{minutes}分間"


class WarningDB:
    def __init__(self):
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL環境変数が設定されていません。")
            
        url = urlparse(DATABASE_URL)
        self.conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require' if 'render.com' in url.hostname else 'prefer'
        )
        self.cursor = self.conn.cursor()
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason TEXT NOT NULL,
                punishment TEXT,
                link TEXT,
                image_url TEXT,
                timestamp TIMESTAMP WITH TIME ZONE
            )
        """)
        self.conn.commit()

    def add_warning(self, user_id, moderator_id, reason, punishment, link, image_url):
        timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        self.cursor.execute("""
            INSERT INTO warnings (user_id, moderator_id, reason, punishment, link, image_url, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (user_id, moderator_id, reason, punishment, link, image_url, timestamp))
        self.conn.commit()
        return self.get_warning_count(user_id)

    def get_warning_count(self, user_id):
        self.cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = %s", (user_id,))
        return self.cursor.fetchone()[0]

    def get_user_warnings(self, user_id):
        self.cursor.execute("SELECT id, reason, timestamp FROM warnings WHERE user_id = %s ORDER BY timestamp DESC", (user_id,))
        return self.cursor.fetchall()

    def delete_last_warning(self, user_id):
        self.cursor.execute("""
            DELETE FROM warnings
            WHERE id = (
                SELECT id FROM warnings WHERE user_id = %s ORDER BY timestamp DESC LIMIT 1
            )
        """, (user_id,))
        deleted_count = self.cursor.rowcount
        self.conn.commit()
        return deleted_count > 0

    def clear_user_warnings(self, user_id):
        self.cursor.execute("DELETE FROM warnings WHERE user_id = %s", (user_id,))
        deleted_count = self.cursor.rowcount
        self.conn.commit()
        return deleted_count

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = WarningDB()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        await self.tree.sync()
        print('Commands synced.')

    @app_commands.command(name="warn", description="違反者に警告を送り、ログに記録します。")
    @app_commands.rename(
        target_user="対象ユーザー",
        content="違反内容",
        punishment="処罰内容",
        link="リンク先",
        image_url="画像url"
    )
    @app_commands.describe(
        target_user="警告をDMで送るユーザー",
        content="違反の具体的な内容",
        punishment="実行する処罰（例: 3日間のミュート、キック）",
        link="該当するメッセージのリンク (任意)",
        image_url="DMとログに添付する画像のURL (任意)"
    )
    async def warn_command(self, interaction: discord.Interaction, target_user: discord.Member, content: str, punishment: str, link: str = "なし", image_url: str = None):
        
        moderator = interaction.user
        warning_count = self.db.add_warning(target_user.id, moderator.id, content, punishment, link, image_url)
        count_display = f"累積{warning_count}回目"
        current_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

        dm_message = f"""
違反回数: {count_display}
違反内容: {content}
処罰内容: {punishment}
該当リンク先: {link}

異議がある場合は いろいろさん or <#1418914386499862628> までご連絡ください。
"""
        
        dm_embed = discord.Embed(
            title="サーバーからの警告",
            description=dm_message,
            color=discord.Color.red(),
            timestamp=current_time
        )
        dm_embed.set_footer(text=f"警告実行者: {moderator.name}")

        if image_url:
            dm_embed.set_image(url=image_url)

        try:
            await target_user.send(embed=dm_embed)
            dm_status = "✅ DM送信成功"
        except discord.Forbidden:
            dm_status = "❌ DM送信失敗 (ユーザーがDMをブロックしている可能性があります)"
        
        log_channel = self.get_channel(LOG_CHANNEL_ID)
        
        log_embed = discord.Embed(
            title="🚨 警告ログ 🚨",
            color=discord.Color.dark_red(),
            timestamp=current_time
        )
        
        log_embed.add_field(name="対象ユーザー", value=f"{target_user.mention} (`{target_user.name}`)", inline=False)
        log_embed.add_field(name="実行モデレーター", value=f"{moderator.mention} (`{moderator.name}`)", inline=False)
        log_embed.add_field(name="DM送信状況", value=dm_status, inline=False)
        
        log_embed.add_field(name="**--- 警告内容 ---**", value="\u200b", inline=False)
        log_embed.add_field(name="違反回数", value=count_display, inline=True)
        log_embed.add_field(name="処罰内容", value=punishment, inline=True)
        log_embed.add_field(name="該当リンク先", value=f"[リンク]({link})" if link != "なし" and link.startswith('http') else link, inline=False)
        log_embed.add_field(name="違反内容詳細", value=content, inline=False)

        if image_url:
            log_embed.set_image(url=image_url)

        if log_channel:
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(
            f"✅ **警告を完了しました。**\n{target_user.mention} にDMで警告を送信しました。現在の累積回数: **{warning_count}回**",
            ephemeral=True
        )

    @app_commands.command(name="punish", description="違反ロールを任意で付与し、指定期間後に自動で解除します。（例: 1d, 12h, 30m）")
    @app_commands.rename(
        target_user="対象ユーザー",
        duration_str="期間_単位付き",
        role_to_add="付与ロール"
    )
    @app_commands.describe(
        target_user="処罰を行うユーザー",
        duration_str="処罰期間。単位: d(日), h(時間), m(分)。例: 7d, 3h, 30m",
        role_to_add="付与する違反ロール (任意)"
    )
    async def punish_command(self, interaction: discord.Interaction, target_user: discord.Member, duration_str: str, role_to_add: discord.Role = None):
        
        duration_minutes = parse_duration(duration_str)
        
        if duration_minutes is None or duration_minutes <= 0:
            await interaction.response.send_message(
                "❌ 期間の入力形式が正しくありません。『日数d, 時間h, 分m』で入力してください。(例: `3h`, `7d`)", 
                ephemeral=True
            )
            return

        display_duration = format_duration(duration_minutes)
        
        if role_to_add:
            if role_to_add.id == MUTE_ROLE_ID:
                await interaction.response.send_message("ミュートロールは管理者が手動で付与・管理してください。", ephemeral=True)
                return

            try:
                await target_user.add_roles(role_to_add, reason=f"モデレーター {interaction.user.name} による一時処罰 ({display_duration})")
                await interaction.response.send_message(
                    f"✅ {target_user.mention} に {role_to_add.name} ロールを **{display_duration}** 付与しました。期間後に自動解除されます。",
                    ephemeral=True
                )
                
                await asyncio.sleep(duration_minutes * 60)
                
                if role_to_add in target_user.roles:
                    await target_user.remove_roles(role_to_add, reason="一時処罰期間終了")
                    await interaction.followup.send(
                        f"✅ {target_user.mention} から {role_to_add.name} ロールを自動解除しました。",
                        ephemeral=False
                    )
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ ロール付与/解除の権限が不足しています。", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ {target_user.mention} にロールを付与せず、期間を **{display_duration}** として記録しました。（このコマンドはロール付与が任意です）",
                ephemeral=True
            )


    @app_commands.command(name="warn_check", description="ユーザーの累積警告回数と詳細を確認します。")
    @app_commands.rename(target_user="対象ユーザー")
    @app_commands.describe(target_user="警告回数を確認するユーザー")
    async def warn_check_command(self, interaction: discord.Interaction, target_user: discord.Member):
        
        warnings = self.db.get_user_warnings(target_user.id)
        count = len(warnings)
        
        if count == 0:
            embed = discord.Embed(title="警告履歴", description=f"{target_user.mention} の警告履歴はありません。", color=discord.Color.green())
        else:
            embed = discord.Embed(title=f"警告履歴 (累積: {count}回)", color=discord.Color.orange())
            for i, (id, reason, timestamp) in enumerate(warnings):
                timestamp_jst = timestamp.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                embed.add_field(
                    name=f"ID: {id} | 警告日時: {timestamp_jst.strftime('%Y/%m/%d %H:%M')}",
                    value=f"内容: {reason[:100]}...",
                    inline=False
                )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="warn_delete", description="対象ユーザーの最新の警告記録を1件削除し、累積回数をリセットします。")
    @app_commands.rename(target_user="対象ユーザー")
    @app_commands.describe(target_user="最新の警告を削除するユーザー")
    async def warn_delete_command(self, interaction: discord.Interaction, target_user: discord.Member):
        
        if self.db.delete_last_warning(target_user.id):
            new_count = self.db.get_warning_count(target_user.id)
            await interaction.response.send_message(
                f"✅ {target_user.mention} の**最新の警告記録を1件削除**しました。現在の累積回数: **{new_count}回**",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ {target_user.mention} に削除できる警告記録はありません。",
                ephemeral=True
            )


    @app_commands.command(name="warn_reset", description="対象ユーザーの全ての警告記録を削除し、累積回数をリセットします。")
    @app_commands.rename(target_user="対象ユーザー")
    @app_commands.describe(target_user="全ての警告をリセットするユーザー")
    async def warn_reset_command(self, interaction: discord.Interaction, target_user: discord.Member):
        
        count = self.db.clear_user_warnings(target_user.id)
        
        if count > 0:
            await interaction.response.send_message(
                f"✅ {target_user.mention} の**全ての警告記録（{count}件）を削除**し、累積回数をリセットしました。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ {target_user.mention} に削除する警告記録はありません。",
                ephemeral=True
            )

intents = discord.Intents.default()
intents.members = True
intents.message_content = False

client = MyClient(intents=intents)

try:
    client.run(TOKEN)
except Exception as e:
    print(f"Botの起動中にエラーが発生しました: {e}")
    print("TOKENまたはDATABASE_URLが正しいか確認してください。")
