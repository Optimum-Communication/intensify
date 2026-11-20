import { Entity, PrimaryGeneratedColumn, Repository } from "typeorm";

@Entity("users")
export class User {
  @PrimaryGeneratedColumn()
  id!: number;
}

export class Users {
  constructor(private readonly repo: Repository<User>) {}

  active(): Promise<User[]> {
    return this.repo.find({ where: { active: true } });
  }

  async remove(id: number): Promise<void> {
    await this.repo.delete(id);
  }

  audit(db: { query(sql: string): unknown }, id: number) {
    return db.query(`INSERT INTO user_audit (user_id) VALUES (${id})`);
  }
}
